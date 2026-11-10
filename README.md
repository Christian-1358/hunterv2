# hunterv2

## Menu de consulta pelo terminal (SQLite ou MySQL)

Por padrão, `consulta_banco.py` cria/abre `hunter.db` em SQLite e preenche um
banco novo com dados fictícios para teste. Não é necessário instalar pacotes
para usar SQLite. Execute:

```bash
python3 consulta_banco.py
```

Para conectar ao MySQL, configure `.env` na raiz do projeto:

```dotenv
db_type=mysql
host=localhost
user=usuario_de_consulta
password=sua_senha
database=nome_do_banco
port=3306
```

Instale o conector MySQL e execute o mesmo comando:

```bash
python3 -m pip install mysql-connector-python
python3 consulta_banco.py
```

Também é possível selecionar `db_type=sqlite` e definir `sqlite_path=./hunter.db`.
O banco SQLite de demonstração contém IMEIs e posições inteiramente fictícios.
Ele só é preenchido quando o arquivo não contém tabelas. A aplicação não migra
automaticamente os dados do MySQL para SQLite.

O menu principal oferece a opção **7 - Adicionar dados a uma tabela**. Escolha
a tabela, preencha os campos solicitados (`Enter` usa o padrão ou grava `NULL`
quando permitido; `:cancelar` interrompe a operação) e confira o resumo. Os
dados só são gravados após confirmar com `s`. Campos numéricos e de data/hora
são validados; chaves auto incrementais e colunas geradas são preenchidas pelo
banco.

Use **2 - Consultar tabela (estrutura e dados)** para ver as colunas e até 100
registros da tabela selecionada. Para uma coluna chamada `ativo`, `active`,
`enabled` ou `is_active`, informe `TRUE`/`FALSE`, `sim`/`não` ou `1`/`0`;
outros valores são recusados.

O menu principal agrupa as consultas em um submenu e as operações de limpeza em
outro. O submenu de limpeza permite simular a retenção, apagar um registro ou
apagar uma tabela inteira. Para apagar um registro, escolha **2 - Apagar dados
dentro de uma tabela**, informe o nome da tabela e selecione a linha exibida.
Essa opção exige uma chave primária e confirmação antes de remover somente a
linha selecionada. Para apagar a tabela inteira, escolha a opção 3, informe o
nome exato e confirme digitando `S` e o nome novamente. A exclusão da tabela
inteira remove todos os seus registros; no MySQL exige permissão `DROP`.

A opção **Consultar logs por mês/ano e dia** pede o IMEI, ano e mês. Informe o
dia para consultar apenas esse dia ou deixe o campo vazio para consultar o mês
inteiro. A busca usa `dateGPS` e exibe até 100 registros.

Na consulta guiada, selecione um IMEI existente na tabela `bem` e depois um
mês/ano dentre as tabelas de log disponíveis para aquele rastreador. A lista de
IMEIs é paginada; use `N` e `A` para avançar e voltar. Em prompts de seleção,
digite `0` para cancelar. Um dia inválido pode ser informado novamente sem
reiniciar a consulta.

No menu principal, escolha **1 - Listar tabelas** para ver os nomes disponíveis.
Também é possível digitar `\tabelas` no prompt `SQL>` e depois escolher uma
tabela para montar uma consulta, por exemplo `SELECT * FROM bem`.

No prompt `SQL>`, você também pode listar tabelas com `SHOW TABLES` (MySQL ou
SQLite) ou, no SQLite, com:

```sql
SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'
```

Uma única `;` no final do comando é aceita. Se deixar o prompt de opção vazio,
o menu informa as opções válidas; use `0` para voltar/sair e `\q` para sair do
prompt de consulta.
