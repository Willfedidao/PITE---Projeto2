import os
import secrets
import sqlite3
from pathlib import Path
from functools import wraps
from decimal import Decimal, InvalidOperation
import click
from flask import Flask, g, session, request, render_template, redirect, url_for, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash

ROOT = Path(__file__).resolve().parent
STATUSES = ('pagamento_simulado', 'em_preparo', 'em_entrega', 'concluido', 'cancelado')

def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(exist_ok=True)
    keyfile = Path(app.instance_path) / 'secret.key'
    if not keyfile.exists():
        try:
            with keyfile.open('x') as f:
                f.write(secrets.token_hex(32))
        except FileExistsError:
            pass
    app.config.update(SECRET_KEY=os.environ.get('SECRET_KEY') or keyfile.read_text(), DATABASE=str(Path(app.instance_path) / 'cupcakes.db'), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=65536)
    if config:
        app.config.update(config)

    def db():
        if 'db' not in g:
            g.db = sqlite3.connect(app.config['DATABASE'], timeout=10)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys = ON')
        return g.db

    @app.teardown_appcontext
    def close_db(error):
        if 'db' in g:
            g.pop('db').close()

    @app.before_request
    def prepare():
        g.user = db().execute('SELECT * FROM usuarios WHERE id=?', (session['uid'],)).fetchone() if session.get('uid') else None
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(24)
        if request.method == 'POST' and not secrets.compare_digest(session['csrf'], request.form.get('csrf', '')):
            abort(400, 'Formulário expirado. Atualize a página e tente novamente.')

    def login_required(admin=False):
        def decorator(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if not g.user:
                    return redirect(url_for('login'))
                if admin and g.user['perfil'] != 'administrador':
                    abort(403)
                return fn(*args, **kwargs)
            return wrapped
        return decorator

    @app.template_filter('money')
    def money(value):
        return f'R$ {value / 100:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

    @app.template_filter('product_image')
    def product_image(flavor):
        flavor = (flavor or '').casefold()
        if 'morango' in flavor:
            image = 'morango'
        elif 'leite' in flavor:
            image = 'doce-de-leite'
        elif 'linguiça' in flavor or 'linguica' in flavor:
            image = 'linguica'
        else:
            image = 'chocolate'
        return f'images/{image}.webp'

    def cart_items():
        items = []
        for pid, qty in session.get('cart', {}).items():
            product = db().execute('SELECT * FROM produtos WHERE id=?', (pid,)).fetchone()
            if product:
                items.append(dict(product, quantidade=qty, subtotal=product['preco_centavos'] * qty))
        return items, sum(p['subtotal'] for p in items)

    @app.context_processor
    def context():
        return dict(csrf=session.get('csrf'), statuses=STATUSES)

    @app.route('/')
    def catalog():
        return render_template('catalog.html', products=db().execute('SELECT * FROM produtos WHERE ativo=1 ORDER BY nome').fetchall())

    @app.route('/produto/<int:pid>')
    def product(pid):
        p = db().execute('SELECT * FROM produtos WHERE id=? AND ativo=1', (pid,)).fetchone()
        if not p:
            abort(404)
        return render_template('product.html', p=p)

    @app.route('/cadastro', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            nome, email, telefone, senha = (request.form.get(k, '').strip() for k in ('nome', 'email', 'telefone', 'senha'))
            senha = request.form.get('senha', '')
            if not nome or '@' not in email or '.' not in email.split('@')[-1] or not telefone or len(senha) < 8:
                flash('Preencha nome, e-mail válido, telefone e senha com pelo menos oito caracteres.', 'error')
            else:
                try:
                    db().execute('INSERT INTO usuarios(nome,email,telefone,senha_hash,perfil) VALUES(?,?,?,?,?)', (nome, email.lower(), telefone, generate_password_hash(senha), 'cliente'))
                    db().commit()
                    flash('Cadastro realizado. Entre com seu e-mail e senha.')
                    return redirect(url_for('login'))
                except sqlite3.IntegrityError:
                    db().rollback()
                    flash('Este e-mail já está cadastrado.', 'error')
        return render_template('auth.html', register=True)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            user = db().execute('SELECT * FROM usuarios WHERE email=?', (request.form.get('email', '').strip(),)).fetchone()
            if user and check_password_hash(user['senha_hash'], request.form.get('senha', '')):
                cart = session.get('cart', {})
                session.clear()
                session.update(uid=user['id'], cart=cart, csrf=secrets.token_hex(24))
                return redirect(url_for('catalog'))
            flash('E-mail ou senha incorretos.', 'error')
        return render_template('auth.html', register=False)

    @app.post('/sair')
    def logout():
        session.clear()
        return redirect(url_for('catalog'))

    @app.route('/carrinho', methods=['GET', 'POST'])
    def cart():
        if request.method == 'POST':
            try:
                pid, qty = int(request.form['produto_id']), int(request.form['quantidade'])
                p = db().execute('SELECT * FROM produtos WHERE id=?', (pid,)).fetchone()
                basket = session.get('cart', {}).copy()
                if request.form.get('acao') == 'adicionar':
                    qty += basket.get(str(pid), 0)
                if qty < 0 or qty > 999:
                    raise ValueError()
                if qty == 0:
                    basket.pop(str(pid), None)
                elif not p or not p['ativo'] or qty > p['estoque']:
                    raise ValueError()
                else:
                    basket[str(pid)] = qty
                session['cart'] = basket
                session.pop('checkout_token', None)
            except (ValueError, KeyError):
                flash('Quantidade inválida ou estoque insuficiente.', 'error')
            return redirect(url_for('cart'))
        items, total = cart_items()
        return render_template('cart.html', items=items, total=total)

    @app.route('/finalizar', methods=['GET', 'POST'])
    @login_required()
    def checkout():
        items, total = cart_items()
        if not items:
            flash('Seu carrinho está vazio.', 'error')
            return redirect(url_for('cart'))
        if request.method == 'POST':
            address = request.form.get('endereco', '').strip()
            if len(address) < 10 or len(address) > 1000:
                flash('Informe um endereço completo de 10 a 1000 caracteres.', 'error')
            elif request.form.get('acao') == 'revisar':
                session['checkout_token'] = secrets.token_hex(24)
                session['checkout_total'] = total
                return render_template('checkout.html', items=items, total=total, address=address, review=True)
            elif request.form.get('acao') == 'confirmar':
                token = session.pop('checkout_token', None)
                if not token or token != request.form.get('token'):
                    flash('Revise o pedido novamente antes de confirmar.', 'error')
                    return redirect(url_for('checkout'))
                conn = db()
                try:
                    conn.execute('BEGIN IMMEDIATE')
                    items, total = cart_items()
                    if not items or total != session.get('checkout_total'):
                        raise ValueError('Os preços mudaram. Revise o pedido novamente.')
                    for p in items:
                        if not p['ativo'] or p['estoque'] < p['quantidade']:
                            raise ValueError('Estoque insuficiente ou produto indisponível. Atualize o carrinho.')
                    cursor = conn.execute('INSERT INTO pedidos(usuario_id,endereco_entrega,status,total_centavos) VALUES(?,?,?,?)', (g.user['id'], address, 'pagamento_simulado', total))
                    order_id = cursor.lastrowid
                    for p in items:
                        conn.execute('INSERT INTO itens_pedido(pedido_id,produto_id,quantidade,preco_unitario_centavos) VALUES(?,?,?,?)', (order_id, p['id'], p['quantidade'], p['preco_centavos']))
                        conn.execute('UPDATE produtos SET estoque=estoque-? WHERE id=?', (p['quantidade'], p['id']))
                    conn.commit()
                except (ValueError, sqlite3.Error) as error:
                    conn.rollback()
                    flash(str(error) if isinstance(error, ValueError) else 'Não foi possível salvar o pedido. Nenhuma compra foi registrada. Tente novamente.', 'error')
                else:
                    session['cart'] = {}
                    flash(f'Pedido #{order_id} confirmado com pagamento simulado!')
                    return redirect(url_for('order_detail', oid=order_id))
        return render_template('checkout.html', items=items, total=total, review=False)

    @app.get('/pedidos')
    @login_required()
    def orders():
        return render_template('orders.html', orders=db().execute('SELECT * FROM pedidos WHERE usuario_id=? ORDER BY id DESC', (g.user['id'],)).fetchall(), admin=False)

    @app.get('/pedidos/<int:oid>')
    @login_required()
    def order_detail(oid):
        order = db().execute('SELECT * FROM pedidos WHERE id=? AND usuario_id=?', (oid, g.user['id'])).fetchone()
        if not order:
            abort(404)
        items = db().execute('SELECT i.*, p.nome FROM itens_pedido i JOIN produtos p ON p.id=i.produto_id WHERE pedido_id=?', (oid,)).fetchall()
        return render_template('order.html', order=order, items=items)

    @app.route('/admin', methods=['GET', 'POST'])
    @login_required(admin=True)
    def admin():
        if request.method == 'POST':
            try:
                price = Decimal(request.form['preco'].replace(',', '.')) * 100
                stock = int(request.form['estoque'])
                name = request.form['nome'].strip()
                if not price.is_finite() or price != price.to_integral_value() or price < 0 or price > 100000000 or stock < 0 or stock > 1000000 or not name:
                    raise ValueError()
                values = (name, request.form.get('descricao', ''), request.form.get('sabor', ''), int(price), stock, int(request.form.get('ativo') == '1'))
                pid = request.form.get('id')
                if pid:
                    db().execute('UPDATE produtos SET nome=?,descricao=?,sabor=?,preco_centavos=?,estoque=?,ativo=? WHERE id=?', values + (int(pid),))
                else:
                    db().execute('INSERT INTO produtos(nome,descricao,sabor,preco_centavos,estoque,ativo) VALUES(?,?,?,?,?,?)', values)
                db().commit()
                flash('Produto salvo.')
            except (ValueError, InvalidOperation, KeyError, sqlite3.Error):
                db().rollback()
                flash('Dados inválidos. Use preço com até duas casas decimais e estoque inteiro não negativo.', 'error')
            return redirect(url_for('admin'))
        edit = db().execute('SELECT * FROM produtos WHERE id=?', (request.args.get('editar'),)).fetchone()
        return render_template('admin.html', products=db().execute('SELECT * FROM produtos ORDER BY id').fetchall(), edit=edit)

    @app.route('/admin/pedidos', methods=['GET', 'POST'])
    @login_required(admin=True)
    def admin_orders():
        if request.method == 'POST':
            if request.form.get('status') not in STATUSES:
                abort(400)
            db().execute('UPDATE pedidos SET status=? WHERE id=?', (request.form['status'], request.form.get('id')))
            db().commit()
            flash('Status atualizado. O status cancelado não repõe estoque automaticamente.')
            return redirect(url_for('admin_orders'))
        orders = db().execute('SELECT p.*,u.nome,u.email FROM pedidos p JOIN usuarios u ON u.id=p.usuario_id ORDER BY p.id DESC').fetchall()
        return render_template('orders.html', orders=orders, admin=True)

    @app.cli.command('init-db')
    @click.option('--demo', is_flag=True, help='Incluir cupcakes fictícios.')
    def init_db(demo):
        path = Path(app.config['DATABASE'])
        if path.exists():
            raise click.ClickException('O banco já existe; nenhum dado foi alterado. Escolha um caminho novo via CUPCAKES_DATABASE.')
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = db()
        conn.executescript((ROOT / 'BD' / 'schema.sql').read_text(encoding='utf-8-sig'))
        if demo:
            conn.executemany('INSERT INTO produtos(nome,descricao,sabor,preco_centavos,estoque) VALUES(?,?,?,?,?)', [('Chocolate Belga', 'Massa macia com cobertura cremosa de chocolate.', 'Chocolate', 1250, 20), ('Morango dos Sonhos', 'Cupcake de baunilha com delicado creme de morango.', 'Morango', 1150, 15), ('Doce de Leite', 'Uma combinação de baunilha e doce de leite.', 'Doce de leite', 1300, 12), ('Linguiça de Tempero Maneiro', 'Cupcake salgado com linguiça e um tempero maneiro de ervas aromáticas.', 'Linguiça de tempero maneiro', 1400, 15)])
        conn.commit()
        click.echo(f'Banco novo criado: {path}')

    @app.cli.command('create-admin')
    @click.option('--nome', prompt=True)
    @click.option('--email', prompt=True)
    @click.password_option(confirmation_prompt=True)
    def create_admin(nome, email, password):
        if len(password) < 8 or '@' not in email or not nome.strip():
            raise click.ClickException('Informe nome, e-mail válido e senha de pelo menos oito caracteres.')
        try:
            db().execute('INSERT INTO usuarios(nome,email,senha_hash,perfil) VALUES(?,?,?,?)', (nome.strip(), email.strip().lower(), generate_password_hash(password), 'administrador'))
            db().commit()
        except sqlite3.IntegrityError:
            db().rollback()
            raise click.ClickException('E-mail já cadastrado. Use outro e-mail; clientes não são promovidos automaticamente.')
        click.echo('Administrador criado.')

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def error_page(error):
        messages = {400: 'Formulário inválido ou expirado. Atualize a página e tente novamente.',
                    403: 'Você não tem permissão para acessar esta área.',
                    404: 'Página ou pedido não encontrado.'}
        return render_template('error.html', code=error.code, message=messages[error.code]), error.code

    app.config['DATABASE'] = os.environ.get('CUPCAKES_DATABASE', app.config['DATABASE']) if not config else app.config['DATABASE']
    return app
