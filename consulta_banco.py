import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path


MAX_ROWS = 100
INVALID_IMEI = "999999999999"
LOG_TABLE_PATTERN = re.compile(r"^gprmc_log_(\d+)_(\d{2})_(\d{4})$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
READ_ONLY_START_PATTERN = re.compile(
    r"^(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN)\b", re.IGNORECASE
)
FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b(INTO|OUTFILE|DUMPFILE|FOR\s+UPDATE|LOCK\s+IN\s+SHARE\s+MODE|"
    r"LOAD_FILE|SLEEP|BENCHMARK)\b",
    re.IGNORECASE,
)

mysql_connector = None


def get_config(name, default=None, cast=str):
    value = os.getenv(name.upper())
    if value is None:
        env_file = Path(__file__).resolve().with_name(".env")
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                key, separator, env_value = line.partition("=")
                if separator and key.strip() == name:
                    value = env_value.strip().strip("\"'")
                    break
    if value is None:
        if default is None:
            raise RuntimeError(f"Configure {name} no ambiente ou no arquivo .env.")
        value = default
    try:
        return cast(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Valor inválido para configuração {name}.") from error


def is_sqlite(connection):
    return isinstance(connection, sqlite3.Connection)


def connect_to_database():
    global mysql_connector
    database_type = os.getenv("DB_TYPE", get_config("db_type", "sqlite")).lower()
    if database_type == "sqlite":
        default_path = Path(__file__).resolve().with_name("hunter.db")
        database_path = Path(
            os.getenv("SQLITE_PATH", get_config("sqlite_path", str(default_path)))
        ).expanduser()
        if not database_path.is_absolute():
            database_path = Path(__file__).resolve().parent / database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        initialize_demo_database(connection)
        print(f"Banco SQLite: {database_path}")
        return connection

    if database_type != "mysql":
        raise ValueError("DB_TYPE deve ser 'sqlite' ou 'mysql'.")

    try:
        import mysql.connector as connector
    except ImportError as error:
        raise RuntimeError(
            "Para usar MySQL, instale mysql-connector-python."
        ) from error
    mysql_connector = connector
    connection = connector.connect(
        host=get_config("host"),
        user=get_config("user"),
        password=get_config("password"),
        database=get_config("database"),
        port=get_config("port", 3306, int),
        connection_timeout=10,
    )
    print("Banco MySQL conectado.")
    return connection


def make_month_table_name(imei, year, month):
    return f"gprmc_log_{imei}_{month:02d}_{year:04d}"


def month_offset(year, month, offset):
    index = year * 12 + month - 1 + offset
    return index // 12, index % 12 + 1


def initialize_demo_database(connection):
    if get_tables(connection):
        return

    now = datetime.now()
    old_year, old_month = month_offset(now.year, now.month, -5)
    recent_year, recent_month = month_offset(now.year, now.month, -2)
    demo_imei = "359881234567890"
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE bem (
                imei TEXT PRIMARY KEY,
                nome TEXT NOT NULL,
                ativo INTEGER NOT NULL
            )
            """
        )
        cursor.executemany(
            "INSERT INTO bem (imei, nome, ativo) VALUES (?, ?, ?)",
            [
                (demo_imei, "Rastreador de teste 1", 1),
                ("359881234567891", "Rastreador de teste 2", 1),
                ("359881234567892", "Rastreador de teste inativo", 0),
            ],
        )
        for year, month in (
            (old_year, old_month),
            (recent_year, recent_month),
            (now.year, now.month),
        ):
            table = make_month_table_name(demo_imei, year, month)
            cursor.execute(
                f"""
                CREATE TABLE `{table}` (
                    id INTEGER PRIMARY KEY,
                    dateGPS TEXT NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    speed REAL
                )
                """
            )
            cursor.executemany(
                f"INSERT INTO `{table}` "
                "(dateGPS, latitude, longitude, speed) VALUES (?, ?, ?, ?)",
                [
                    (f"{year:04d}-{month:02d}-05 12:00:00", -23.5505, -46.6333, 0.0),
                    (f"{year:04d}-{month:02d}-15 08:30:00", -23.5610, -46.6550, 42.5),
                ],
            )
        connection.commit()
        print("Banco de demonstração inicializado com dados fictícios.")
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        cursor.close()


def make_cursor(connection, dictionary=False):
    if is_sqlite(connection):
        return connection.cursor()
    return connection.cursor(dictionary=dictionary)


def quote_identifier(identifier):
    return f"`{identifier.replace('`', '``')}`"


def database_error_types():
    if mysql_connector is None: 
        return (sqlite3.Error,)
    return (sqlite3.Error, mysql_connector.Error)


def get_tables(connection, pattern=None):
    cursor = make_cursor(connection)
    try:
        if is_sqlite(connection):
            query = (
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
            parameters = ()
        else:
            query = (
                "SELECT TABLE_NAME FROM information_schema.tables "
                "WHERE TABLE_SCHEMA = DATABASE()"
            )
            parameters = ()
        if pattern is not None:
            query += " AND name LIKE ?" if is_sqlite(connection) else " AND TABLE_NAME LIKE %s"
            parameters = (pattern,)
        query += " ORDER BY 1"
        cursor.execute(query, parameters)
        return [row[0] for row in cursor.fetchall()]
    finally:
        cursor.close()


def parse_log_table(table_name):
    match = LOG_TABLE_PATTERN.fullmatch(table_name)
    if match is None:
        return None

    imei, month_text, year_text = match.groups()
    month = int(month_text)
    year = int(year_text)
    if imei == INVALID_IMEI or not 1 <= month <= 12:
        return None
    if not 2020 <= year <= datetime.now().year:
        return None
    return imei, year, month


def display_rows(cursor, rows, numbered=False):
    if not rows:
        print("Nenhum resultado encontrado.")
        return

    data_column_names = [column[0] for column in cursor.description]
    normalized_rows = [
        dict(row)
        if isinstance(row, (sqlite3.Row, dict))
        else dict(zip(data_column_names, row))
        for row in rows
    ]
    column_names = (["#"] if numbered else []) + data_column_names
    widths = {
        name: min(
            50,
            max(
                len(str(name)),
                *(
                    len(str(index if name == "#" else row.get(name, "")))
                    for index, row in enumerate(normalized_rows, start=1)
                ),
            ),
        )
        for name in column_names
    }

    print(" | ".join(name[:widths[name]].ljust(widths[name]) for name in column_names))
    print("-+-".join("-" * widths[name] for name in column_names))
    for index, row in enumerate(normalized_rows, start=1):
        print(
            " | ".join(
                str(index if name == "#" else row.get(name, ""))[:widths[name]].ljust(widths[name])
                for name in column_names
            )
        )


def list_tables(connection):
    tables = get_tables(connection)
    if not tables:
        print("Nenhuma tabela encontrada.")
        return
    print(f"\nTabelas encontradas: {len(tables)}")
    for table in tables:
        print(f"- {table}")


def show_table_details(connection):
    tables = get_tables(connection)
    if not tables:
        print("Nenhuma tabela encontrada.")
        return
    for index, table in enumerate(tables[:MAX_ROWS], start=1):
        print(f"{index} - {table}")
    while True:
        choice = input("Número da tabela (0 para cancelar): ").strip()
        if choice == "0":
            return
        if choice.isdigit() and 1 <= int(choice) <= min(len(tables), MAX_ROWS):
            break
        print("Opção inválida. Digite um número da lista ou 0 para cancelar.")

    table = tables[int(choice) - 1]
    cursor = make_cursor(connection, dictionary=True)
    try:
        print(f"\nEstrutura da tabela {table}:")
        if is_sqlite(connection):
            cursor.execute(f"PRAGMA table_xinfo({quote_identifier(table)})")
        else:
            cursor.execute(f"SHOW COLUMNS FROM `{table}`")
        display_rows(cursor, cursor.fetchall())

        print(f"\nDados da tabela {table}:")
        cursor.execute(f"SELECT * FROM {quote_identifier(table)} LIMIT {MAX_ROWS + 1}")
        rows = cursor.fetchall()
        display_rows(cursor, rows[:MAX_ROWS])
        if len(rows) > MAX_ROWS:
            print(f"\nExibindo somente as primeiras {MAX_ROWS} linhas.")
    finally:
        cursor.close()


def get_column_definitions(connection, table):
    cursor = make_cursor(connection)
    try:
        if is_sqlite(connection):
            cursor.execute(f"PRAGMA table_xinfo({quote_identifier(table)})")
            definitions = []
            for row in cursor.fetchall():
                definitions.append(
                    {
                        "name": row[1],
                        "type": (row[2] or "").upper(),
                        "nullable": not bool(row[3]) and not bool(row[5]),
                        "default": row[4],
                        "generated": bool(row[6])
                        or (bool(row[5]) and (row[2] or "").upper() == "INTEGER"),
                    }
                )
            return definitions

        cursor.execute(f"SHOW COLUMNS FROM `{table}`")
        definitions = []
        for row in cursor.fetchall():
            definitions.append(
                {
                    "name": row[0],
                    "type": (row[1] or "").upper(),
                    "nullable": row[2] == "YES",
                    "default": row[4],
                    "generated": "GENERATED" in (row[5] or "").upper()
                    or "AUTO_INCREMENT" in (row[5] or "").upper(),
                }
            )
        return definitions
    finally:
        cursor.close()


def parse_column_value(raw_value, definition):
    value = raw_value.strip()
    column_type = definition["type"]
    column_name = definition["name"].lower()

    if value.upper() == "NULL":
        if definition["nullable"]:
            return None
        raise ValueError("esta coluna não aceita NULL")

    is_boolean = column_type in {"BOOLEAN", "BOOL"} or column_name in {
        "ativo",
        "active",
        "enabled",
        "is_active",
    }
    if is_boolean:
        if value.lower() in {"true", "1", "sim", "s"}:
            return 1
        if value.lower() in {"false", "0", "nao", "não", "n"}:
            return 0
        raise ValueError("digite TRUE/FALSE, sim/não ou 1/0")

    if "INT" in column_type:
        return int(value)
    if any(kind in column_type for kind in ("REAL", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC")):
        return float(value.replace(",", "."))

    if "DATETIME" in column_type or "TIMESTAMP" in column_type:
        datetime.fromisoformat(value.replace(" ", "T"))
    elif column_type == "DATE":
        datetime.strptime(value, "%Y-%m-%d")
    elif column_type == "TIME":
        datetime.strptime(value, "%H:%M:%S")
    return value


def add_table_data(connection):
    tables = get_tables(connection)
    if not tables:
        print("Nenhuma tabela encontrada.")
        return

    for index, table in enumerate(tables[:MAX_ROWS], start=1):
        print(f"{index} - {table}")
    if len(tables) > MAX_ROWS:
        print(f"Seleção limitada às primeiras {MAX_ROWS} tabelas.")

    while True:
        choice = input("Número da tabela (0 para cancelar): ").strip()
        if choice == "0":
            print("Inserção cancelada.")
            return
        if choice.isdigit() and 1 <= int(choice) <= min(len(tables), MAX_ROWS):
            table = tables[int(choice) - 1]
            break
        print("Opção inválida. Escolha um número listado ou 0 para cancelar.")

    definitions = get_column_definitions(connection, table)
    insertable_columns = [column for column in definitions if not column["generated"]]
    if not insertable_columns:
        print("A tabela não possui colunas que aceitem inserção manual.")
        return

    values = {}
    for definition in insertable_columns:
        name = definition["name"]
        details = f"tipo {definition['type'] or 'texto'}"
        if definition["default"] is not None:
            details += ", Enter usa o valor padrão"
        elif definition["nullable"]:
            details += ", Enter grava NULL"
        else:
            details += ", obrigatório"

        while True:
            raw_value = input(f"{name} ({details}; :cancelar para sair): ")
            if raw_value.strip().lower() == ":cancelar":
                print("Inserção cancelada.")
                return
            if not raw_value.strip() and not definition["nullable"] and definition["default"] is None:
                print(f"{name} é obrigatório. Digite um valor ou :cancelar.")
                continue
            if not raw_value.strip() and definition["default"] is not None:
                break
            if not raw_value.strip() and definition["nullable"]:
                values[name] = None
                break
            try:
                values[name] = parse_column_value(raw_value, definition)
                break
            except ValueError as error:
                print(f"Valor inválido para {name}: {error}. Tente novamente.")

    print(f"\nRegistro para inserir em {table}:")
    for name, value in values.items():
        print(f"- {name}: {value!r}")
    if input("Confirmar inserção? (s/N): ").strip().lower() != "s":
        print("Inserção cancelada. Nenhum dado foi gravado.")
        return

    columns = list(values)
    if columns:
        quoted_columns = ", ".join(quote_identifier(name) for name in columns)
        placeholders = ", ".join(
            "?" if is_sqlite(connection) else "%s" for _ in columns
        )
        query = (
            f"INSERT INTO {quote_identifier(table)} "
            f"({quoted_columns}) VALUES ({placeholders})"
        )
        parameters = tuple(values[name] for name in columns)
    else:
        if is_sqlite(connection):
            query = f"INSERT INTO {quote_identifier(table)} DEFAULT VALUES"
        else:
            query = f"INSERT INTO {quote_identifier(table)} () VALUES ()"
        parameters = ()

    cursor = make_cursor(connection)
    try:
        cursor.execute(query, parameters)
        connection.commit()
        print("Registro adicionado com sucesso.")
    except database_error_types():
        connection.rollback()
        raise
    finally:
        cursor.close()


def list_imeis(connection):
    page_text = input("Página (100 IMEIs por página; padrão 1): ").strip() or "1"
    if not page_text.isdigit() or int(page_text) < 1:
        print("Informe um número de página válido.")
        return

    placeholder = "?" if is_sqlite(connection) else "%s"
    cursor = make_cursor(connection, dictionary=True)
    try:
        cursor.execute(
            "SELECT DISTINCT imei FROM bem WHERE imei IS NOT NULL "
            f"ORDER BY imei LIMIT {placeholder} OFFSET {placeholder}",
            (MAX_ROWS, (int(page_text) - 1) * MAX_ROWS),
        )
        rows = cursor.fetchall()
        if not rows:
            print("Nenhum IMEI encontrado nessa página.")
            return
        print(f"\nIMEIs (página {page_text}):")
        for row in rows:
            print(f"- {row['imei']}")
    finally:
        cursor.close()


def choose_imei(connection):
    placeholder = "?" if is_sqlite(connection) else "%s"
    page = 1
    while True:
        cursor = make_cursor(connection)
        try:
            cursor.execute(
                "SELECT DISTINCT imei FROM bem "
                f"WHERE imei IS NOT NULL ORDER BY imei LIMIT {placeholder} OFFSET {placeholder}",
                (MAX_ROWS, (page - 1) * MAX_ROWS),
            )
            imeis = [str(row[0]) for row in cursor.fetchall()]
        finally:
            cursor.close()

        if not imeis:
            if page == 1:
                print("Não há IMEIs cadastrados na tabela bem.")
            else:
                print("Não há mais IMEIs nesta página.")
                page -= 1
                continue
            return None

        print(f"\nIMEIs cadastrados — página {page}:")
        for index, imei in enumerate(imeis, start=1):
            print(f"{index} - {imei}")
        print("N - Próxima página")
        if page > 1:
            print("A - Página anterior")
        print("0 - Cancelar")

        choice = input("Escolha o IMEI pelo número: ").strip().lower()
        if choice == "0":
            return None
        if choice == "n":
            if len(imeis) == MAX_ROWS:
                page += 1
            else:
                print("Você já está na última página.")
            continue
        if choice == "a" and page > 1:
            page -= 1
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(imeis):
            imei = imeis[int(choice) - 1]
            if imei.isdigit() and imei != INVALID_IMEI:
                return imei
            print("O IMEI selecionado não pode ser usado para consultar logs.")
            continue
        print("Opção inválida. Escolha um IMEI listado ou navegue pelas páginas.")


def list_log_tables_for_imei(connection):
    imei = input("IMEI: ").strip()
    if not imei.isdigit() or imei == INVALID_IMEI:
        print("IMEI inválido.")
        return
    tables = get_tables(connection, f"gprmc_log_{imei}_%")
    valid_tables = [table for table in tables if parse_log_table(table)]
    if not valid_tables:
        print("Nenhuma tabela mensal válida encontrada para esse IMEI.")
        return
    for table in valid_tables:
        print(f"- {table}")


def query_logs_by_date(connection):
    imei = choose_imei(connection)
    if imei is None:
        print("Consulta cancelada.")
        return

    tables = get_tables(connection, f"gprmc_log_{imei}_%")
    available_tables = []
    for table in tables:
        parsed = parse_log_table(table)
        if parsed is not None and parsed[0] == imei:
            available_tables.append((table, parsed[1], parsed[2]))
    available_tables.sort(key=lambda item: (item[1], item[2]), reverse=True)

    if not available_tables:
        print(f"Nenhuma tabela mensal de log encontrada para o IMEI {imei}.")
        return

    print(f"\nMeses disponíveis para o IMEI {imei}:")
    for index, (_, year, month) in enumerate(available_tables, start=1):
        print(f"{index} - {month:02d}/{year}")
    while True:
        choice = input("Escolha o mês/ano (0 para cancelar): ").strip()
        if choice == "0":
            print("Consulta cancelada.")
            return
        if choice.isdigit() and 1 <= int(choice) <= len(available_tables):
            table, year, month = available_tables[int(choice) - 1]
            break
        print("Opção inválida. Escolha um mês da lista ou 0 para cancelar.")

    while True:
        day_text = input(
            "Dia (1-31; Enter para o mês inteiro; 0 para cancelar): "
        ).strip()
        if day_text == "0":
            print("Consulta cancelada.")
            return
        try:
            if day_text:
                start_date = datetime(year, month, int(day_text))
                end_date = start_date + timedelta(days=1)
            else:
                start_date = datetime(year, month, 1)
                next_year, next_month = month_offset(year, month, 1)
                end_date = datetime(next_year, next_month, 1)
            break
        except (ValueError, TypeError):
            print("Dia inválido para esse mês. Tente novamente ou digite 0 para cancelar.")

    placeholder = "?" if is_sqlite(connection) else "%s"
    quoted_table = f"`{table}`"
    cursor = make_cursor(connection, dictionary=True)
    try:
        cursor.execute(
            f"SELECT * FROM {quoted_table} WHERE dateGPS >= {placeholder} "
            f"AND dateGPS < {placeholder} ORDER BY dateGPS LIMIT {MAX_ROWS + 1}",
            (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
        )
        rows = cursor.fetchall()
        display_rows(cursor, rows[:MAX_ROWS])
        if len(rows) > MAX_ROWS:
            print(f"\nResultado limitado às primeiras {MAX_ROWS} linhas.")
        print(f"Tabela consultada: {table}")
    finally:
        cursor.close()


def run_read_only_query(connection):
    dialect = "SQLite" if is_sqlite(connection) else "MySQL"
    print(f"Banco conectado: {dialect}")
    print("Digite SELECT para consultar dados ou \\tabelas para listar as tabelas.")
    if is_sqlite(connection):
        print("Exemplo: SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    else:
        print("Exemplo para listar tabelas: SHOW TABLES")
    print("Também pode usar SHOW TABLES; digite \\q para voltar.")
    sql = input("SQL> ").strip()
    if sql.lower() in {"\\q", "\\sair"}:
        return
    if sql.lower() in {"\\tabelas", "\\tables", "show tables", "show tables;"}:
        list_tables(connection)
        return
    if not sql:
        print("Consulta vazia. Digite um SELECT, \\tabelas para listar tabelas ou \\q para voltar.")
        return
    if sql.endswith(";"):
        sql = sql[:-1].rstrip()
    if (
        ";" in sql
        or "--" in sql
        or "/*" in sql
        or "*/" in sql
        or not READ_ONLY_START_PATTERN.match(sql)
        or FORBIDDEN_SQL_PATTERN.search(sql)
    ):
        print("Consulta recusada: informe um único comando de consulta somente leitura.")
        return

    cursor = make_cursor(connection, dictionary=True)
    try:
        if (
            is_sqlite(connection)
            and re.fullmatch(r"SHOW\s+TABLES", sql, re.IGNORECASE)
        ):
            sql = (
                "SELECT name AS name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        cursor.execute(sql)
        if cursor.description is None:
            print("A consulta não retornou uma tabela de resultados.")
            return
        rows = cursor.fetchmany(MAX_ROWS + 1)
        display_rows(cursor, rows[:MAX_ROWS])
        if len(rows) > MAX_ROWS:
            print(f"\nResultado limitado às primeiras {MAX_ROWS} linhas.")
    except database_error_types() as error:
        print(f"Erro ao executar a consulta: {error}")
    finally:
        cursor.close()


def get_retention_months():
    while True:
        value = input("Retenção (3 ou 6 meses; 0 para cancelar): ").strip()
        if value in {"3", "6"}:
            return int(value)
        if value == "0":
            print("Operação cancelada.")
            return None
        print("Opção inválida. Digite 3, 6 ou 0 para cancelar.")


def find_expired_tables(connection, retention_months):
    now = datetime.now()
    cutoff_index = now.year * 12 + now.month - 1 - retention_months - 1
    expired = []
    for table in get_tables(connection, "gprmc_log_%"):
        parsed = parse_log_table(table)
        if parsed is None:
            continue
        _, year, month = parsed
        if year * 12 + month - 1 <= cutoff_index:
            expired.append(table)
    return expired


def show_cleanup_preview(tables, retention_months):
    print(
        f"\nRetenção: mês atual + {retention_months} meses fechados. "
        f"Candidatas válidas: {len(tables)}"
    )
    for table in tables[:20]:
        print(f"- {table}")
    if len(tables) > 20:
        print(f"... e mais {len(tables) - 20} tabelas.")


def simulate_cleanup(connection):
    retention = get_retention_months()
    if retention is None:
        return
    tables = find_expired_tables(connection, retention)
    show_cleanup_preview(tables, retention)
    print("Simulação concluída: nenhuma tabela foi removida.")


def delete_table_by_name(connection):
    tables = get_tables(connection)
    if not tables:
        print("Nenhuma tabela encontrada para excluir.")
        return

    print("\nTabelas disponíveis:")
    for table in tables:
        print(f"- {table}")

    table_name = input(
        "\nDigite exatamente o nome da tabela que deseja apagar "
        "(:cancelar para voltar): "
    ).strip()
    if table_name.lower() == ":cancelar":
        print("Exclusão cancelada.")
        return
    if table_name not in tables or not IDENTIFIER_PATTERN.fullmatch(table_name):
        print("Nome não encontrado na lista. Nenhuma tabela foi apagada.")
        return

    print(
        f"\nATENÇÃO: apagar a tabela '{table_name}' remove permanentemente "
        "todos os dados dela."
    )
    if input("Tem certeza? Digite S para continuar: ").strip().lower() != "s":
        print("Exclusão cancelada. Nenhuma tabela foi apagada.")
        return
    confirmation = input(f"Digite o nome exato '{table_name}' para confirmar: ").strip()
    if confirmation != table_name:
        print("O nome não corresponde. Nenhuma tabela foi apagada.")
        return

    cursor = make_cursor(connection)
    try:
        cursor.execute(f"DROP TABLE {quote_identifier(table_name)}")
        connection.commit()
        print(f"Tabela '{table_name}' apagada com sucesso.")
    except database_error_types():
        connection.rollback()
        raise
    finally:
        cursor.close()


def get_primary_key_columns(connection, table_name):
    cursor = make_cursor(connection)
    try:
        if is_sqlite(connection):
            cursor.execute(
                f"PRAGMA table_xinfo({quote_identifier(table_name)})"
            )
            primary_key_columns = [
                (row[5], row[1])
                for row in cursor.fetchall()
                if row[5]
            ]
            return [
                name
                for _, name in sorted(primary_key_columns)
            ]

        cursor.execute(f"SHOW COLUMNS FROM {quote_identifier(table_name)}")
        return [
            row[0]
            for row in cursor.fetchall()
            if row[3] == "PRI"
        ]
    finally:
        cursor.close()


def delete_row_from_table(connection):
    tables = get_tables(connection)
    if not tables:
        print("Nenhuma tabela encontrada.")
        return

    print("\nTabelas disponíveis:")
    for table in tables:
        print(f"- {table}")

    table_name = input(
        "\nDigite o nome exato da tabela (:cancelar para voltar): "
    ).strip()
    if table_name.lower() == ":cancelar":
        print("Operação cancelada.")
        return
    if table_name not in tables or not IDENTIFIER_PATTERN.fullmatch(table_name):
        print("Nome não encontrado na lista. Nenhum dado foi apagado.")
        return

    primary_key_columns = get_primary_key_columns(connection, table_name)
    if not primary_key_columns:
        print(
            "Não é seguro apagar uma linha desta tabela porque ela não tem "
            "chave primária."
        )
        return

    cursor = make_cursor(connection, dictionary=True)
    try:
        cursor.execute(
            f"SELECT * FROM {quote_identifier(table_name)} "
            f"LIMIT {MAX_ROWS + 1}"
        )
        rows = cursor.fetchall()
        if not rows:
            print("A tabela não contém registros.")
            return

        if len(rows) > MAX_ROWS:
            rows = rows[:MAX_ROWS]
            print(f"Exibindo as primeiras {MAX_ROWS} linhas.")
        display_rows(cursor, rows, numbered=True)
    finally:
        cursor.close()

    while True:
        choice = input(
            "\nNúmero da coluna # para apagar (Enter ou 0 para cancelar): "
        ).strip()
        if choice in {"", "0"}:
            print("Operação cancelada.")
            return
        if choice.isdigit() and 1 <= int(choice) <= len(rows):
            selected_row = rows[int(choice) - 1]
            break
        print(f"Escolha um número entre 1 e {len(rows)}, ou 0 para cancelar.")

    print("\nRegistro selecionado:")
    selected_row_data = dict(selected_row)
    for column, value in selected_row_data.items():
        print(f"- {column}: {value!r}")

    confirmation = input(
        f"Você tem certeza que quer apagar este registro da tabela "
        f"'{table_name}'? Digite S para confirmar: "
    ).strip().lower()
    if confirmation != "s":
        print("Operação cancelada. Nenhum dado foi apagado.")
        return

    where_clause = " AND ".join(
        f"{quote_identifier(column)} = "
        f"{'?' if is_sqlite(connection) else '%s'}"
        for column in primary_key_columns
    )
    parameters = tuple(selected_row_data[column] for column in primary_key_columns)
    cursor = make_cursor(connection)
    try:
        cursor.execute(
            f"DELETE FROM {quote_identifier(table_name)} WHERE {where_clause}",
            parameters,
        )
        if cursor.rowcount != 1:
            connection.rollback()
            print(
                "O registro não foi apagado: a chave primária não corresponde "
                "a exatamente uma linha."
            )
            return
        connection.commit()
        print("Registro apagado com sucesso.")
    except database_error_types():
        connection.rollback()
        raise
    finally:
        cursor.close()


def run_submenu(title, options):
    while True:
        print(f"\n=== {title} ===")
        for key, (label, _) in options.items():
            print(f"{key} - {label}")
        print("0 - Voltar")
        choice = input("Escolha uma opção: ").strip()
        if choice == "0":
            return
        if not choice:
            print(
                f"Digite {', '.join(options)} para escolher uma opção, "
                "ou 0 para voltar."
            )
            continue
        selected = options.get(choice)
        if selected is None:
            print("Opção inválida. Tente novamente.")
            continue
        try:
            selected[1]()
        except database_error_types() as error:
            print(f"Erro no banco de dados: {error}")


def run_menu(connection):
    while True:
        print("\n=== Menu principal do banco HUNTER ===")
        print("1 - Listar tabelas")
        print("2 - Consultar tabela (estrutura e dados)")
        print("3 - Listar IMEIs da tabela bem")
        print("4 - Listar tabelas de log de um IMEI")
        print("5 - Menu de consultas")
        print("6 - Menu de limpeza")
        print("7 - Adicionar dados a uma tabela")
        print("0 - Sair")
        option = input("Escolha uma opção: ").strip()
        if option == "0":
            print("Encerrando.")
            return
        if not option:
            print("Digite uma opção de 1 a 8 ou 0 para sair.")
            continue

        direct_actions = {
            "1": lambda: list_tables(connection),
            "2": lambda: show_table_details(connection),
            "3": lambda: list_imeis(connection),
            "4": lambda: list_log_tables_for_imei(connection),
            "7": lambda: add_table_data(connection),
        }
        try:
            if option in direct_actions:
                direct_actions[option]()
            elif option == "5":
                run_submenu(
                    "Menu de consultas",
                    {
                        "1": ("Executar consulta somente leitura", lambda: run_read_only_query(connection)),
                        "2": ("Consultar logs por mês/ano e dia", lambda: query_logs_by_date(connection)),
                    },
                )
            elif option == "6":
                run_submenu(
                    "Menu de limpeza",
                    {
                        "1": ("Simular limpeza por retenção", lambda: simulate_cleanup(connection)),
                        "2": ("Apagar dados dentro de uma tabela", lambda: delete_row_from_table(connection)),
                        "3": ("Apagar uma tabela inteira pelo nome", lambda: delete_table_by_name(connection)),
                    },
                )
            else:
                print("Opção inválida. Tente novamente.")
        except database_error_types() as error:
            print(f"Erro no banco de dados: {error}")


def main():
    connection = None
    try:
        connection = connect_to_database()
        run_menu(connection)
    except (sqlite3.Error, RuntimeError, ValueError, OSError) as error:
        print(f"Não foi possível iniciar o menu: {error}", file=sys.stderr)
        return 1
    except database_error_types() as error:
        print(f"Erro de conexão com o banco de dados: {error}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
