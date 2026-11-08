import re
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from app import create_app


class ShopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'test.db')
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only', 'DATABASE': self.path})
        result = self.app.test_cli_runner().invoke(args=['init-db', '--demo'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.path)) as conn:
            with conn:
                return conn.execute(query, args).fetchall()

    def post(self, url, data=None, client=None):
        client = client or self.client
        client.get('/')
        with client.session_transaction() as session:
            token = session['csrf']
        return client.post(url, data=dict(data or {}, csrf=token), follow_redirects=True)

    def account(self, email='ana@example.com', client=None):
        return self.post('/cadastro', dict(nome='Ana', email=email, telefone='11999999999', senha='senha12345', perfil='administrador'), client)

    def login(self, email='ana@example.com', client=None):
        return self.post('/login', dict(email=email, senha='senha12345'), client)

    def add(self, qty=2):
        return self.post('/carrinho', dict(produto_id=1, quantidade=qty, acao='adicionar'))

    def review(self):
        response = self.post('/finalizar', dict(endereco='Rua dos Cupcakes, 123, Centro, São Paulo SP 01000-000', acao='revisar'))
        self.assertIn('Revise seu pedido', response.text)
        with self.client.session_transaction() as session:
            return session['checkout_token']

    def confirm(self, token):
        return self.post('/finalizar', dict(endereco='Rua dos Cupcakes, 123, Centro, São Paulo SP 01000-000', acao='confirmar', token=token))

    def test_complete_flow_and_private_orders(self):
        self.account()
        row = self.sql('SELECT senha_hash,perfil FROM usuarios')[0]
        self.assertNotEqual(row[0], 'senha12345')
        self.assertEqual(row[1], 'cliente')
        self.assertIn('já está cadastrado', self.account('ANA@example.com').text)
        self.login()
        self.add()
        with self.client.session_transaction() as session:
            self.assertEqual(session['cart'], {'1': 2})
        response = self.confirm(self.review())
        self.assertIn('Pedido #1', response.text)
        self.assertEqual(self.sql('SELECT estoque FROM produtos WHERE id=1')[0][0], 18)
        self.assertEqual(self.client.get('/produto/1').status_code, 200)
        self.assertEqual(self.sql('SELECT total_centavos,status FROM pedidos')[0], (2500, 'pagamento_simulado'))
        self.assertEqual(self.sql('SELECT quantidade,preco_unitario_centavos FROM itens_pedido')[0], (2, 1250))
        self.assertIn('Pedido #1', self.client.get('/pedidos').text)
        second = self.app.test_client()
        self.account('outro@example.com', second)
        self.login('outro@example.com', second)
        self.assertEqual(second.get('/pedidos/1').status_code, 404)
        self.assertNotIn('Pedido #1', second.get('/pedidos').text)
        self.assertEqual(self.client.get('/admin').status_code, 403)
        self.assertEqual(self.post('/admin', {'nome': 'Ataque'}).status_code, 403)
        self.assertEqual(self.post('/admin/pedidos', {'status': 'concluido', 'id': '1'}).status_code, 403)
        self.post('/sair')
        self.assertEqual(self.client.get('/pedidos').status_code, 302)

    def test_insufficient_stock_rechecked(self):
        self.account(); self.login(); self.add()
        token = self.review()
        self.sql('UPDATE produtos SET estoque=1 WHERE id=1')
        self.assertIn('Estoque insuficiente', self.confirm(token).text)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM pedidos')[0][0], 0)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM itens_pedido')[0][0], 0)
        self.assertEqual(self.sql('SELECT estoque FROM produtos WHERE id=1')[0][0], 1)
        self.assertIn('estoque insuficiente', self.add(5).text)

    def test_failure_rolls_back_entire_order(self):
        self.account(); self.login(); self.add()
        self.post('/carrinho', dict(produto_id=2, quantidade=1, acao='adicionar'))
        token = self.review()
        self.sql("CREATE TRIGGER fail_second BEFORE INSERT ON itens_pedido WHEN NEW.produto_id=2 BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.assertIn('Nenhuma compra foi registrada', self.confirm(token).text)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM pedidos')[0][0], 0)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM itens_pedido')[0][0], 0)
        self.assertEqual(self.sql('SELECT estoque FROM produtos WHERE id=1')[0][0], 20)

    def test_admin_and_init_protection(self):
        result = self.app.test_cli_runner().invoke(args=['create-admin', '--nome', 'Admin', '--email', 'admin@example.com', '--password', 'senha12345'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.login('admin@example.com')
        self.assertEqual(self.client.get('/admin').status_code, 200)
        self.post('/admin', dict(nome='Teste', preco='14,99', estoque=5, descricao='Teste', sabor='Limão', ativo='1'))
        self.assertEqual(self.sql('SELECT preco_centavos FROM produtos WHERE nome=?', ('Teste',))[0][0], 1499)
        self.post('/admin', dict(id=5, nome='Teste', preco='15,00', estoque=0, descricao='Teste', sabor='Limão'))
        self.assertEqual(self.sql('SELECT ativo,estoque FROM produtos WHERE id=5')[0], (0, 0))
        self.assertEqual(self.client.get('/produto/5').status_code, 404)
        self.assertEqual(self.client.get('/admin/pedidos').status_code, 200)
        self.add(1)
        self.confirm(self.review())
        self.post('/admin/pedidos', dict(id=1, status='em_preparo'))
        self.assertEqual(self.sql('SELECT status FROM pedidos')[0][0], 'em_preparo')
        self.assertEqual(self.post('/admin/pedidos', dict(id=1, status='inventado')).status_code, 400)
        self.assertNotEqual(self.app.test_cli_runner().invoke(args=['init-db', '--demo']).exit_code, 0)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM produtos')[0][0], 5)

    def test_validation_csrf_update_and_double_submit(self):
        self.assertEqual(self.client.post('/cadastro').status_code, 400)
        self.post('/cadastro', dict(nome='Ana', email='ana@example.com', telefone='123', senha='curta'))
        self.assertEqual(self.sql('SELECT COUNT(*) FROM usuarios')[0][0], 0)
        self.account(); self.login(); self.add()
        self.post('/carrinho', dict(produto_id=1, quantidade=3))
        with self.client.session_transaction() as session:
            self.assertEqual(session['cart'], {'1': 3})
        self.post('/carrinho', dict(produto_id=1, quantidade=0))
        with self.client.session_transaction() as session:
            self.assertEqual(session['cart'], {})
        self.add()
        token = self.review()
        self.confirm(token)
        self.confirm(token)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM pedidos')[0][0], 1)

    def test_price_change_and_inactive_product(self):
        self.account(); self.login(); self.add()
        token = self.review()
        self.sql('UPDATE produtos SET preco_centavos=1500 WHERE id=1')
        self.assertIn('preços mudaram', self.confirm(token).text)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM pedidos')[0][0], 0)
        token = self.review()
        self.sql('UPDATE produtos SET ativo=0 WHERE id=1')
        self.assertIn('produto indisponível', self.confirm(token).text)
        self.assertEqual(self.sql('SELECT estoque FROM produtos WHERE id=1')[0][0], 20)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM pedidos')[0][0], 0)


if __name__ == '__main__':
    unittest.main()
