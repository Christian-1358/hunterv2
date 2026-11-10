HUNTER - LEVANTAMENTO PARA MIGRAÇÃO DE LOGS (gprmc_log) PARA SERVIDOR DE LOGS
Data do levantamento: 06/11/2026
Servidor: HUNTER (174.138.54.205, SSH porta 46824) - MySQL 5.7.42
==========================================================================

1) SITUAÇÃO ATUAL
-----------------
- Disco /: 310 GB, 130 GB usados (42%).
- Pasta /var/lib/mysql/tracker: 114 GB.
- Tabelas de log: gprmc_log_<IMEI>_<MES>_<ANO>  (mês real, 1-12)
  - Uma tabela por rastreador por mês.
  - Particionada por dia do dateGPS: p0 (dias 1-6), p1 (7-14), p2 (15-22), p3 (23-31).
  - O mês seguinte já fica pré-criado vazio (~5 GB de estrutura por mês).
- innodb_file_per_table = ON  (cada tabela/partição tem seu próprio .ibd)
- ibdata1 = 460 MB (pequeno).
- log_bin = OFF.
- Só existem dados de out/2026 em diante (resultado da última limpeza manual).

2) CONSUMO MEDIDO
-----------------
- Outubro/2026 (mês completo): 89,2 GB em disco (~84 GB dados + ~5 GB estrutura) = ~2,7 GB/dia
- Novembro/2026 (1 a 6/11):    ~14,3 GB de dados = ~2,5 GB/dia
- Frota: 9.094 -> 9.181 rastreadores (~+1%/mês)

=> MÉDIA: ~85-90 GB POR MÊS (~2,6-2,8 GB/dia)

3) PROJEÇÃO DE VOLUME PARA O SERVIDOR DE LOGS
---------------------------------------------
(conta mês corrente + meses fechados, pois convivem até a limpeza; +~25% folga)

  Retenção 3 meses -> 4 meses simultâneos -> ~360 GB bruto -> RECOMENDADO 500 GB
  Retenção 6 meses -> 7 meses simultâneos -> ~630 GB bruto -> RECOMENDADO 800 GB

4) LIMPEZA - PROCESSO ATUAL (MANUAL)
------------------------------------
Snap -> sobe em outro servidor -> backup do tracker SEM logs -> apaga banco tracker
-> apaga ibdata1 -> restaura backup sem logs -> puxa 1 mês de log do snap.

5) LIMPEZA - PROPOSTA (DROP POR MÊS)
------------------------------------
- Como é uma tabela por mês, basta DROP TABLE das tabelas do mês que saiu da retenção.
- Com innodb_file_per_table=ON, o DROP apaga o .ibd e libera o espaço na hora
  (um mês inteiro ~89 GB).
- O que o DROP NÃO libera: espaço dentro do ibdata1 - hoje só 460 MB,
  ou seja, o processo manual ganha no máximo ~0,5 GB a mais que o DROP.
- Onde o "lixo" que o suporte citou é real:
    * DELETE FROM (não DROP) -> arquivo não encolhe (precisa OPTIMIZE TABLE)
    * Tabelas criadas com file_per_table desligado -> ficam no ibdata1
    * ibdata1 inchado por undo de transações longas
  Nenhum desses é o caso hoje no HUNTER.
- Se um teste de DROP pareceu liberar pouco: conferir se mediu com df (não du)
  e se algum processo segurava os arquivos abertos (lsof +L1).
- Cuidados do script:
    * DROP em lotes (ex.: 50 tabelas, pausa 1-2 s), de madrugada
      (no 5.7 cada DROP trava rapidamente o buffer pool).
    * Opcional: mysqldump comprimido do mês antes do DROP (arquivo morto) -> elimina o snap.
    * Ignorar tabelas fora do padrão: anos estranhos (2019, 2029), IMEI 999999999999,
      gprmc_recent, gprmc_grid, rota_precisa.
    * Agendar no cron (ex.: dia 1º apaga mês M-4 p/ 3 meses, ou M-7 p/ 6 meses).
- Prova sugerida: no snap, df antes/depois de dropar um mês inteiro.

6) CONFIG SUGERIDA PARA O SERVIDOR DE LOGS NOVO (my.cnf)
--------------------------------------------------------
Evita que o ibdata1 inche -> não precisa mais apagar ibdata1 / restaurar.

  [mysqld]
  innodb_file_per_table      = ON
  innodb_undo_tablespaces    = 2      # undo fora do ibdata1 (SÓ vale na criação da instância)
  innodb_undo_log_truncate   = ON     # encolhe undo automaticamente
  innodb_max_undo_log_size   = 1G
  innodb_temp_data_file_path = ibtmp1:12M:autoextend:max:5G

ATENÇÃO: innodb_undo_tablespaces precisa estar no my.cnf ANTES de iniciar o MySQL
pela primeira vez. Se a migração for copiando o datadir, não vale -> migrar via dump/restore.

Rotina proposta:
  1. Mensal: DROP em lotes dos meses fora da retenção.
  2. Sem reinicializar instância.
  3. Alerta se ibdata1 > 2 GB (só então considerar o procedimento manual).

7) PENDÊNCIAS / PRÓXIMOS PASSOS
-------------------------------
[ ] Definir retenção: 3 ou 6 meses
[ ] Definir se arquiva (mysqldump) antes do DROP
[ ] Preparar my.cnf do servidor de logs
[ ] Preparar script de limpeza mensal (modo simulação + lotes)
[ ] Testar DROP de um mês no snap (df antes/depois) para validar com o suporte
[ ] Confirmar a taxa diária de novembro alguns dias depois