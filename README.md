# Cupcakes Gourmet — PITE II

Aplicação acadêmica local com Python, Flask, Jinja e SQLite (`sqlite3`). Interface responsiva, cadastro e login, catálogo, carrinho, revisão de entrega, pagamento simulado, pedidos privados e administração.

## Rodar no PowerShell do Windows

Instale o Python 3.11 ou superior pelo site https://www.python.org/downloads/windows/ (marque a opção de instalar o launcher `py`). Abra um novo PowerShell depois da instalação. Execute **um comando de cada vez**:

```powershell
Set-Location 'C:\Users\Will-PC\PIT SOFTWARE'
```

```powershell
py --version
```

```powershell
py -m venv .venv
```

Não é necessário ativar o ambiente; os comandos abaixo usam o executável dele diretamente, evitando problemas com a política de execução do PowerShell.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Para criar o banco novo com quatro cupcakes fictícios:

```powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app init-db --demo
```

O banco será `instance\cupcakes.db`. Para um banco vazio, use `init-db` sem `--demo`. **Execute a inicialização apenas uma vez**: se o arquivo já existir, o comando recusa a operação sem alterar registros.

Crie a primeira conta administrativa pelo terminal:

```powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app create-admin
```

Informe nome, e-mail e senha de pelo menos oito caracteres; a senha fica oculta e é solicitada duas vezes. Use um e-mail ainda não cadastrado. Cadastros pelo site sempre criam clientes, mesmo que alguém tente enviar um perfil diferente.

Inicie o site:

```powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app run --host 127.0.0.1 --port 5000
```

Mantenha esse terminal aberto. Abra **outro PowerShell** e execute:

```powershell
Start-Process 'http://127.0.0.1:5000'
```

Para encerrar, pressione `Ctrl+C` no terminal do servidor. Nas próximas execuções, basta entrar na pasta e executar o comando `flask ... run`; não inicialize o banco novamente. Este servidor destina-se a desenvolvimento local.

## Conferir o funcionamento

1. Clique em Criar conta, preencha nome, e-mail, telefone e senha; depois faça login.
2. Abra os detalhes de um cupcake, adicione ao carrinho e experimente atualizar a quantidade ou informar zero para remover.
3. Clique em Continuar para entrega, informe o endereço completo, revise o resumo e confirme com **pagamento simulado**. Não há cobrança, cartão ou PIX real.
4. Confira o número do pedido, os itens e o status em Meus pedidos. A quantidade disponível no catálogo deve diminuir.
5. Saia e entre como administrador. Em Produtos • Admin, cadastre, edite, desative produtos e ajuste o estoque. Em Pedidos • Admin, atualize o status.
6. Entre com outro cliente: o pedido anterior não aparece e o endereço `/pedidos/1` retorna 404 se esse pedido pertencer a outra conta.

Status disponíveis: pagamento simulado, em preparo, em entrega, concluído e cancelado. **Cancelar muda somente o status**; se houver devolução, o administrador ajusta o estoque manualmente. Não há reembolso real.

## Testes automatizados em banco separado

Na pasta do projeto, execute:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Os testes criam bancos temporários e os removem ao final. Verificam cadastro, duplicidade de e-mail, hash e perfil, login/saída, carrinho, pedido e preços em centavos, redução de estoque, isolamento entre clientes, bloqueio administrativo, CSRF, edição/desativação de produtos e inicialização sem sobrescrita. Simulam estoque alterado entre a revisão e a confirmação e falha no segundo item para conferir o rollback de todo o pedido.

## Banco original e estrutura

`BD\schema.sql` é a fonte das quatro tabelas: `usuarios`, `produtos`, `pedidos` e `itens_pedido`. Não foi necessária migração. `BD\pit-sofwates.db` e seus registros são preservados: a aplicação usa por padrão **outro banco**, `instance\cupcakes.db`. Não copie o banco novo por cima do antigo.

Valores são inteiros em centavos; `itens_pedido.preco_unitario_centavos` preserva o preço da compra. Cada conexão ativa `PRAGMA foreign_keys = ON`. A confirmação usa `BEGIN IMMEDIATE`, relê preços, disponibilidade e estoque e grava pedido, itens e desconto de estoque numa única transação. Erros provocam rollback. Mudanças de preço exigem nova revisão.

Para escolher outro banco novo, antes de `init-db`, defina um caminho diferente (não use o do banco original):

```powershell
$env:CUPCAKES_DATABASE = Join-Path (Get-Location) 'instance\outro-banco.db'
```

Os comandos subsequentes no mesmo terminal usarão esse caminho. Para voltar ao padrão:

```powershell
Remove-Item Env:CUPCAKES_DATABASE
```

## Conferir no DB Browser for SQLite

Instale o DB Browser for SQLite em https://sqlitebrowser.org/. Depois de confirmar um pedido:

1. Abra o DB Browser, clique em **Open Database / Abrir banco** e selecione `C:\Users\Will-PC\PIT SOFTWARE\instance\cupcakes.db` (ou o caminho personalizado).
2. Na aba **Browse Data / Navegar dados**, escolha `pedidos`. Confira `id`, `usuario_id`, `endereco_entrega`, `status` e `total_centavos`.
3. Escolha `itens_pedido`. O `pedido_id` corresponde ao `id` do pedido; confira `produto_id`, `quantidade` e `preco_unitario_centavos`.
4. Em `produtos`, confira a redução do estoque. Valores como `1250` representam R$ 12,50.
5. Use Refresh / Atualizar para ver compras novas. Evite editar e manter transações abertas enquanto usa o site, pois isso pode bloquear gravações.

Uma consulta opcional na aba Execute SQL / Executar SQL:

```sql
SELECT p.id AS pedido, u.nome AS cliente, pr.nome AS cupcake,
       i.quantidade, i.preco_unitario_centavos, p.total_centavos, p.status
FROM pedidos p
JOIN usuarios u ON u.id = p.usuario_id
JOIN itens_pedido i ON i.pedido_id = p.id
JOIN produtos pr ON pr.id = i.produto_id
ORDER BY p.id, i.id;
```

## Segurança e limites desta versão

Senhas são armazenadas apenas como hash pelo Werkzeug. Há consultas parametrizadas, validação no servidor, proteção CSRF, escape HTML pelo Jinja e verificação de perfil/propriedade no servidor. O carrinho na sessão assinada contém apenas IDs e quantidades; preços vêm do banco. A chave local é gerada em `instance\secret.key` na primeira execução; opcionalmente pode ser fornecida pela variável de ambiente `SECRET_KEY`.

`.gitignore` exclui bancos, arquivos auxiliares SQLite, chaves locais, `.env` e ambientes virtuais. Se um banco já tiver sido versionado antes, `.gitignore` não o remove do histórico: não publique bancos com dados pessoais. Guarde cópias de segurança fora do Git.

Recuperação de senha, CEP automático e pagamentos reais ficam para a próxima etapa. Datas do SQLite são exibidas em UTC. Esta versão local não inclui envio de mensagens, frete, restauração automática de estoque ao cancelar, limitação de tentativas de login ou configuração de produção com HTTPS.

## Verificação realizada nesta entrega

Em 08/11/2026, os **6 testes automatizados passaram** com Python 3.13 e as versões de dependências fixadas em `requirements.txt`. Também foi verificada resposta HTTP 200 do catálogo, CSS, detalhes, cadastro, login e carrinho usando servidor local e banco temporário. Foram conferidas mudanças de preço, produto desativado e atualização administrativa de status. Não foi feita inspeção visual em navegador; o comportamento responsivo foi implementado em CSS, para você conferir em janela estreita ou no modo de dispositivo do navegador.

O ambiente desta entrega não tinha `py`/`python` no PATH. Uma cópia auxiliar do Python foi baixada em `.runtime` apenas para executar as verificações, com essa pasta excluída do Git. Para o uso cotidiano, siga a instalação padrão e os comandos de `.venv` acima. Nenhum pedido de teste foi gravado no banco original ou no banco de uso do site.

## Imagens ilustrativas do site

A página inicial usa a frase “Pequenos bolinhos e grandes sabores.” e uma imagem de confeitaria fictícia. Catálogo e detalhes exibem imagens ilustrativas dos quatro sabores. Os arquivos WebP em `static/images/` foram gerados por IA para este projeto e são servidos localmente, sem depender de sites externos. A seleção é feita pelo sabor: morango, doce de leite, linguiça e chocolate. Sabores novos sem correspondência recebem a ilustração padrão de chocolate; ajuste o filtro `product_image` em `app.py` para acrescentar outra imagem.
