import mysql.connector
from datetime import datetime, timedelta
from decouple import config

class DeleteScript(object):

    # Funcao para calcular os ultimos 12 meses
    def calculate_last_12_months(self):
        today = datetime.today()
        months = []
        
        for i in range(12):
            month = today - timedelta(days=i * 30) 
            month_year = f'{month.month:02}/{month.year}'
            months.append(month_year)
        
        return months

    # Funcao para buscar todos os IMEIs do banco de dados
    def get_all_imeis(self, connection, limit, offset):
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT DISTINCT imei FROM bem limit {} offset {}".format(limit, offset))
            rows = cursor.fetchall()
            return [row['imei'] for row in rows if row['imei'] is not None]
        except mysql.connector.Error as err:
            print(f"Erro ao buscar IMEIs: {err}")
            raise

    # Funcao para exibir todos os meses de cada IMEI
    def display_months_for_imeis(self,connection, limit, offset):
        try:
            imeis = self.get_all_imeis(connection, limit, offset)
            
            if not imeis:
                print('Nenhum IMEI encontrado no banco de dados.')
                return

            print(f'IMEIs encontrados: {imeis}')

            months = self.calculate_last_12_months()
            for imei in imeis:
                print(f"\nMeses para o IMEI {imei}:")
                hoje = datetime.now()
                form = '{}/{}'.format(str(hoje.month).zfill(2), str(hoje.year))
                for month in months:
                    if form == month:
                        print("MES ATUAL NAO DELETAR !")
                    else:
                        table_name = f"gprmc_log_{imei}_{month.replace('/', '_')}"
                        print(f"  {table_name}")
                        self.drop_table(table_name, connection)
        except Exception as err:
            print(f"Erro ao exibir meses para IMEIs: {err}")


    def drop_table(self, table, connection):
        try:
            print("Dropando Tabela {}".format(table))
            cursor = connection.cursor(dictionary=False)
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
            connection.commit()
            print(f"Tabela {table} foi excluida com sucesso!")
        except:
            print("Tabela nao existente!")


    # Funcao principal para conectar ao banco e executar a logica
    def test_connection(self, limit, offset):
        connection = None
        try:
            print('Tentando conectar ao banco de dados...')
            config_ = {
                    'host': config('host'),
                    'user': config('user'),
                    'password': config('password'),
                    'database': config('database'),
                }
            connection = mysql.connector.connect(**config_)
            print('Conexao com o banco de dados estabelecida com sucesso!')

            # Exibir meses para cada IMEI
            self.display_months_for_imeis(connection, limit, offset)

        except mysql.connector.Error as err:
            print(f"Erro ao conectar ao banco de dados: {err}")
            exit(1) 

        finally:
            if connection:
                connection.close()
                print('Conexao encerrada.')

if __name__ == '__main__':
    dt = DeleteScript()
    dt.test_connection()