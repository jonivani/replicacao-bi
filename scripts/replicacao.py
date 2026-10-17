"""
Replicação incremental (DIFF) e completa (COMPLETO) de tabelas Protheus
entre dois SQL Servers independentes (sem Linked Server).

Como funciona:
  - DIFF    : conecta nos dois servidores separadamente, compara pelo maior
              S_T_A_M_P_ e faz inserção/atualização por R_E_C_N_O_; sem STAMP, insere novos RECNOs.
  - COMPLETO: prepara uma tabela auxiliar e substitui a tabela de destino ao concluir.

Uso:
    python replicacao.py --modo diff
    python replicacao.py --modo completo
    python replicacao.py --modo diff --tabela SC5010
"""

import pyodbc
import configparser
import logging
import argparse
import sys
import os
import time  # Adicionado para medição precisa de tempo por tabela
import concurrent.futures
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / 'config' / 'config.ini'


def resolver_caminho(caminho: str | Path | None, fallback: Path | None = None) -> Path:
    """Resolve caminhos relativos considerando a raiz do projeto."""
    if caminho is None:
        return fallback if fallback is not None else ROOT_DIR

    path = Path(caminho)
    if path.is_absolute():
        return path

    candidatos = [Path.cwd() / path, ROOT_DIR / path, ROOT_DIR / 'config' / path]
    for candidato in candidatos:
        if candidato.exists():
            return candidato.resolve()

    return (fallback if fallback is not None else (ROOT_DIR / path)).resolve()


# Importa módulo de notificação por e-mail
try:
    from email_notificacao import RelatorioReplicacao, NotificadorEmail
    EMAIL_DISPONIVEL = True
except ImportError:
    EMAIL_DISPONIVEL = False

import db_historico
import speedtest_servico

# ---------------------------------------------------------------------------
# Lista de tabelas Protheus que serão replicadas.
# ---------------------------------------------------------------------------
# A edição pública exige mapeamento explícito em config/config.ini.
TABELAS_DIFF_DEFAULT: list[str] = []
TABELAS_COMPLETO_DEFAULT: list[str] = []


def carregar_lista_tabelas(cfg: configparser.ConfigParser, opcao: str, padrao: list[str]) -> list[str]:
    """Lê a lista de tabelas do config.ini (seção [tabelas]), com fallback para a lista padrão."""
    if cfg.has_section('tabelas') and cfg.has_option('tabelas', opcao):
        val = cfg.get('tabelas', opcao).strip()
        if val:
            return [t.strip().upper() for t in val.split(',') if t.strip()]
    return padrao


# ===========================================================================
# LOGGING
# ===========================================================================
def configurar_log(log_dir: str | Path, modo: str) -> tuple[logging.Logger, str]:
    """
    Configura logging e retorna o logger e o caminho do arquivo de log.
    """
    base_log_dir = Path(log_dir)
    if not base_log_dir.is_absolute():
        base_log_dir = ROOT_DIR / base_log_dir

    caminho_pasta = base_log_dir / modo
    caminho_pasta.mkdir(parents=True, exist_ok=True)

    nome_arquivo = caminho_pasta / f"replicacao_{modo}_{datetime.now():%Y%m%d_%H%M%S}.log"

    logger = logging.getLogger('replicacao')
    logger.setLevel(logging.INFO)

    if logger.hasHandlers():
        logger.handlers.clear()

    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    fh = logging.FileHandler(nome_arquivo, encoding='utf-8')
    fh.setFormatter(fmt)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger, str(nome_arquivo)

# ===========================================================================
# RESILIÊNCIA & RETRY
# ===========================================================================
def executar_com_retry(func, *args, max_tentativas=3, espera_inicial=3, **kwargs):
    """Executa uma função com lógica de retry e recuo exponencial."""
    import time as time_module
    tentativa = 1
    espera = espera_inicial
    while True:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if tentativa >= max_tentativas:
                raise e
            logging.getLogger('replicacao').warning(
                f"Falha na tentativa {tentativa} de {max_tentativas} ({e}). Tentando novamente em {espera}s..."
            )
            time_module.sleep(espera)
            tentativa += 1
            espera *= 2


# ===========================================================================
# CONEXÃO
# ===========================================================================
def get_conexao(cfg: configparser.ConfigParser, secao: str) -> pyodbc.Connection:
    s = cfg[secao] if secao in cfg else {}
    
    # Fallback para variáveis de ambiente
    prefixo = 'DB_ORIGEM_' if secao == 'origem' else 'DB_DESTINO_'
    servidor = os.environ.get(prefixo + 'SERVER') or s.get('server', '')
    porta    = os.environ.get(prefixo + 'PORT') or s.get('port', '1433')
    database = os.environ.get(prefixo + 'DB') or s.get('database', '')
    username = os.environ.get(prefixo + 'USER') or s.get('username', '')
    password = os.environ.get(prefixo + 'PASS') or s.get('password', '')
    driver   = os.environ.get(prefixo + 'DRIVER') or s.get('driver', 'ODBC Driver 17 for SQL Server')
    
    if ',' in servidor or '\\' in servidor:
        server_str = servidor
    else:
        server_str = f"{servidor},{porta}"

    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server_str};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        f"TrustServerCertificate=yes;"
        f"Encrypt=yes;"
        f"Connection Timeout={cfg['configuracao'].get('timeout_conexao', '30')};"
    )

    conn = pyodbc.connect(conn_str, autocommit=False)
    conn.timeout = int(cfg['configuracao'].get('timeout_query', '0'))
    return conn


def get_conexao_com_retry(cfg: configparser.ConfigParser, secao: str) -> pyodbc.Connection:
    max_tentativas = int(cfg['configuracao'].get('dias_retry_attempts', '3'))
    return executar_com_retry(get_conexao, cfg, secao, max_tentativas=max_tentativas, espera_inicial=3)


# ===========================================================================
# LOCK DE EXECUÇÃO — impede duas instâncias (DIFF/COMPLETO, disparadas por
# qualquer via: tarefa agendada, painel web ou linha de comando) de rodarem
# ao mesmo tempo contra o mesmo destino
# ===========================================================================
NOME_LOCK_EXECUCAO = 'ReplicacaoBI_Execucao'


class ExecucaoBloqueadaError(Exception):
    """Levantada quando não foi possível obter o lock de execução: já existe outra instância rodando."""


def adquirir_lock_execucao(cfg: configparser.ConfigParser, timeout_ms: int | None = None) -> pyodbc.Connection | None:
    """
    Obtém um lock exclusivo (sp_getapplock) no banco de destino, escopo 'Session'.

    Sem esse lock, duas execuções concorrentes (ex.: a tarefa agendada do Windows disparando
    junto com um clique manual no painel, ou dois disparos seguidos) recalculam o mesmo ponto
    de corte (MAX(S_T_A_M_P_) / MAX(R_E_C_N_O_)) e inserem os mesmos registros duas vezes no
    destino — a causa raiz da duplicidade que este lock resolve.

    Escopo 'Session': o lock fica preso à conexão que o obteve e é liberado automaticamente pelo
    SQL Server se o processo cair ou a conexão for fechada, sem deixar lock órfão. O timeout é
    curto de propósito: se outra execução já está rodando, a intenção é pular este ciclo (o
    próximo DIFF pega tudo que ficou pendente) em vez de enfileirar e atrasar em cascata.
    """
    if timeout_ms is None:
        timeout_ms = int(cfg['configuracao'].get('lock_timeout_ms', '5000'))

    conn = get_conexao_com_retry(cfg, 'destino')
    try:
        cur = conn.cursor()
        cur.execute(
            "DECLARE @res int; "
            "EXEC @res = sp_getapplock @Resource = ?, @LockMode = 'Exclusive', "
            "@LockOwner = 'Session', @LockTimeout = ?; "
            "SELECT @res;",
            NOME_LOCK_EXECUCAO, timeout_ms
        )
        resultado = cur.fetchone()[0]
        if resultado is not None and resultado >= 0:
            return conn
    except Exception:
        conn.close()
        raise

    conn.close()
    return None


def liberar_lock_execucao(conn: pyodbc.Connection | None) -> None:
    """Libera o lock de execução fechando a conexão que o mantém (lock de escopo 'Session')."""
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


# ===========================================================================
# SCHEMA — leitura e recriação de estrutura de tabela
# ===========================================================================
_TIPOS_COM_TAMANHO  = {'char', 'varchar', 'nchar', 'nvarchar', 'binary', 'varbinary'}
_TIPOS_COM_PRECISAO = {'decimal', 'numeric'}

def _tipo_sql(data_type: str, max_len, precision, scale) -> str:
    t = data_type.lower()
    if t in _TIPOS_COM_TAMANHO:
        tam = 'MAX' if max_len == -1 else str(max_len)
        return f'{data_type}({tam})'
    if t in _TIPOS_COM_PRECISAO:
        return f'{data_type}({precision},{scale})'
    return data_type


def get_schema_tabela(conn: pyodbc.Connection, tabela: str) -> list[dict]:
    cur = conn.cursor()
    cur.execute("""
        SELECT COLUMN_NAME, DATA_TYPE,
               CHARACTER_MAXIMUM_LENGTH,
               NUMERIC_PRECISION, NUMERIC_SCALE,
               IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
    """, tabela)

    colunas = []
    for row in cur.fetchall():
        colunas.append({
            'nome':      row[0],
            'tipo':      _tipo_sql(row[1], row[2], row[3], row[4]),
            'nullable':  row[5] == 'YES',
            'data_type': row[1].lower(),
        })
    return colunas


# Precisão fracionária padrão de cada tipo de data/hora do SQL Server.
# DATETIME arredonda para incrementos de ~3.33ms (3 casas), DATETIME2/TIME suportam até 7.
_ESCALA_DATETIME_PADRAO = {
    'datetime': 3, 'smalldatetime': 0, 'datetime2': 7, 'time': 7, 'date': 0,
}
_PYODBC_TIPOS_DATA = {
    'datetime': pyodbc.SQL_TYPE_TIMESTAMP,
    'smalldatetime': pyodbc.SQL_TYPE_TIMESTAMP,
    'datetime2': pyodbc.SQL_TYPE_TIMESTAMP,
    'time': pyodbc.SQL_TYPE_TIME,
    'date': pyodbc.SQL_TYPE_DATE,
}


def montar_input_sizes_datetime(colunas: list[dict]) -> list | None:
    """Monta a lista para cursor.setinputsizes(), fixando tipo/precisão apenas das colunas
    de data/hora (as demais ficam None, com detecção automática do pyodbc).

    Necessário para usar fast_executemany=True com segurança: sem isso, o pyodbc decide o
    tipo/precisão de cada parâmetro olhando só a 1a linha do lote. Se essa linha tiver
    S_T_A_M_P_ com .000, as demais linhas do MESMO lote perdem a fração de segundo ao
    gravar — o que quebra a comparação incremental do DIFF (MAX(S_T_A_M_P_) fica truncado).
    """
    sizes = [
        (_PYODBC_TIPOS_DATA[c['data_type']], 23, _ESCALA_DATETIME_PADRAO.get(c['data_type'], 3))
        if c['data_type'] in _PYODBC_TIPOS_DATA else None
        for c in colunas
    ]
    return sizes if any(s is not None for s in sizes) else None


def criar_tabela(cur: pyodbc.Cursor, tabela: str, colunas: list[dict]) -> None:
    defs = []
    for c in colunas:
        nulo = 'NULL' if c['nullable'] else 'NOT NULL'
        defs.append(f"    [{c['nome']}] {c['tipo']} {nulo}")

    # PRIMARY KEY em R_E_C_N_O_: sem isso nada no banco impede duas linhas com o mesmo recno.
    # É a segunda camada de proteção contra duplicidade, complementar ao lock de execução (que
    # evita que duas execuções cheguem a rodar ao mesmo tempo) — se algo mesmo assim escapar do
    # lock, o INSERT/MERGE falha alto em vez de duplicar silenciosamente.
    if any(c['nome'].upper() == 'R_E_C_N_O_' for c in colunas):
        defs.append(f"    CONSTRAINT [PK_{tabela}] PRIMARY KEY CLUSTERED ([R_E_C_N_O_])")

    sql = f"CREATE TABLE [{tabela}] (\n" + ',\n'.join(defs) + '\n)'
    cur.execute(sql)


def tabela_existe(conn: pyodbc.Connection, tabela: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = ?",
        tabela
    )
    return cur.fetchone()[0] > 0


def comparar_schemas(colunas_origem: list[dict], colunas_destino: list[dict]) -> bool:
    if len(colunas_origem) != len(colunas_destino):
        return False
    for i in range(len(colunas_origem)):
        if (colunas_origem[i]['nome'] != colunas_destino[i]['nome'] or 
            colunas_origem[i]['tipo'] != colunas_destino[i]['tipo']):
            return False
    return True


# ===========================================================================
# DIFF — insere apenas registros novos (incremental)
# ===========================================================================
def diff_tabela(conn_orig: pyodbc.Connection, conn_dest: pyodbc.Connection,
                tabela: str, batch_size: int, log: logging.Logger) -> int:
    """
    Replica registros novos ou modificados com verificação de Schema Evolution.
    Suporta de forma híbrida tabelas com ou sem a coluna S_T_A_M_P_.
    """
    t_inicio = time.perf_counter()  # Início do contador individual
    cur_dest = conn_dest.cursor()
    cur_orig = conn_orig.cursor()
    cur_orig.arraysize = batch_size

    colunas_origem = get_schema_tabela(conn_orig, tabela)
    if not colunas_origem:
        log.error(f"  {tabela}: tabela não encontrada na origem, pulando.")
        return 0

    # 1. Verifica existência e compara estrutura
    precisa_criar = False
    if tabela_existe(conn_dest, tabela):
        colunas_destino = get_schema_tabela(conn_dest, tabela)
        if not comparar_schemas(colunas_origem, colunas_destino):
            log.warning(f"  {tabela}: Estrutura alterada. Recriando tabela no destino...")
            cur_dest.execute(f"DROP TABLE [dbo].[{tabela}]")
            precisa_criar = True
    else:
        precisa_criar = True

    if precisa_criar:
        criar_tabela(cur_dest, tabela, colunas_origem)
        conn_dest.commit()

    # Detecta se a tabela possui S_T_A_M_P_
    nomes_colunas = {c['nome'].upper() for c in colunas_origem}
    tem_stamp = 'S_T_A_M_P_' in nomes_colunas

    if tem_stamp:
        # Lógica Incremental Inteligente por S_T_A_M_P_ (UPSERT)
        cur_dest.execute(f"SELECT ISNULL(MAX(S_T_A_M_P_), '1900-01-01 00:00:00') FROM [dbo].[{tabela}]")
        max_stamp = cur_dest.fetchone()[0]

        # FIX: O Protheus geralmente usa DATETIME. No SQL Server, DATETIME arredonda para incrementos
        # de 3.33ms (ex: .033 é armazenado internamente como .0333333). O pyodbc lê isso e 
        # converte para Python datetime como .033000. Ao passar de volta como parâmetro, ele vai como 
        # DATETIME2 (.0330000). Assim, S_T_A_M_P_ (.0333333) > Parâmetro (.0330000) resulta em VERDADEIRO,
        # causando loop infinito. O CAST(? AS DATETIME) força o parâmetro a sofrer o mesmo arredondamento.
        cur_orig.execute(f"SELECT COUNT(*) FROM [dbo].[{tabela}] WITH(NOLOCK) WHERE S_T_A_M_P_ > CAST(? AS DATETIME)", max_stamp)
        total_novos = cur_orig.fetchone()[0]

        if total_novos == 0:
            t_total = time.perf_counter() - t_inicio
            log.info(f"  {tabela} [STAMP]: sem novos registros. S_T_A_M_P_ BI={max_stamp} (Tempo: {t_total:.2f}s)")
            return 0

        # Log de diagnóstico: mostra o ponto de corte e o S_T_A_M_P_ mais recente no Prod
        cur_orig.execute(f"SELECT MAX(S_T_A_M_P_) FROM [dbo].[{tabela}] WITH(NOLOCK) WHERE S_T_A_M_P_ > CAST(? AS DATETIME)", max_stamp)
        max_stamp_prod = cur_orig.fetchone()[0]
        log.info(f"  {tabela} [STAMP]: {total_novos} registro(s) a processar. "
                 f"BI max={max_stamp} | Prod max={max_stamp_prod}")

        cur_orig.execute(f"SELECT * FROM [dbo].[{tabela}] WITH(NOLOCK) WHERE S_T_A_M_P_ > CAST(? AS DATETIME) ORDER BY S_T_A_M_P_", max_stamp)

        n_colunas = len(cur_orig.description)
        placeholders = ','.join(['?' for _ in range(n_colunas)])
        
        # Cria staging table no destino
        staging_tabela = f"staging_{tabela}"
        
        # Garante a exclusão caso já exista de uma execução anterior abortada
        cur_dest.execute(f"IF OBJECT_ID('dbo.[{staging_tabela}]', 'U') IS NOT NULL DROP TABLE dbo.[{staging_tabela}]")
        conn_dest.commit()
        
        cur_dest.execute(f"SELECT TOP 0 * INTO dbo.[{staging_tabela}] FROM [dbo].[{tabela}]")
        conn_dest.commit()
        
        insert_staging_sql = f"INSERT INTO dbo.[{staging_tabela}] VALUES ({placeholders})"

        # Constrói o MERGE dinâmico
        colunas_insert = []
        colunas_values = []
        colunas_update = []
        for c in colunas_origem:
            col_name = f"[{c['nome']}]"
            colunas_insert.append(col_name)
            colunas_values.append(f"source.{col_name}")
            if c['nome'].upper() != 'R_E_C_N_O_':
                colunas_update.append(f"target.{col_name} = source.{col_name}")

        merge_sql = f"""
        MERGE [dbo].[{tabela}] AS target
        USING dbo.[{staging_tabela}] AS source
        ON (target.[R_E_C_N_O_] = source.[R_E_C_N_O_])
        WHEN MATCHED THEN
            UPDATE SET {', '.join(colunas_update)}
        WHEN NOT MATCHED THEN
            INSERT ({', '.join(colunas_insert)})
            VALUES ({', '.join(colunas_values)});
        """

        inseridos = 0
        cur_dest.fast_executemany = True
        input_sizes = montar_input_sizes_datetime(colunas_origem)
        if input_sizes is not None:
            try:
                cur_dest.setinputsizes(input_sizes)
            except Exception as e:
                log.warning(f"  {tabela}: setinputsizes falhou ({e}), desativando fast_executemany.")
                cur_dest.fast_executemany = False

        try:
            while True:
                rows = cur_orig.fetchmany(batch_size)
                if not rows:
                    break
                try:
                    cur_dest.executemany(insert_staging_sql, rows)
                except Exception as e:
                    log.warning(f"  {tabela}: Erro ao inserir na staging. Tentando gravação padrão... Detalhe: {e}")
                    cur_dest.fast_executemany = False
                    cur_dest.executemany(insert_staging_sql, rows)
                
                cur_dest.execute(merge_sql)
                cur_dest.execute(f"TRUNCATE TABLE dbo.[{staging_tabela}]")
                conn_dest.commit()
                inseridos += len(rows)
        finally:
            try:
                cur_dest.execute(f"IF OBJECT_ID('dbo.[{staging_tabela}]', 'U') IS NOT NULL DROP TABLE dbo.[{staging_tabela}]")
                conn_dest.commit()
            except Exception:
                pass

    else:
        # Lógica Fallback por R_E_C_N_O_ (Somente Inserção)
        cur_dest.execute(f"SELECT ISNULL(MAX(R_E_C_N_O_), 0) FROM [dbo].[{tabela}]")
        max_recno = cur_dest.fetchone()[0]

        cur_orig.execute(f"SELECT COUNT(*) FROM [dbo].[{tabela}] WITH(NOLOCK) WHERE R_E_C_N_O_ > ?", max_recno)
        total_novos = cur_orig.fetchone()[0]

        if total_novos == 0:
            t_total = time.perf_counter() - t_inicio
            log.info(f"  {tabela} [RECNO]: sem novos registros. (Tempo: {t_total:.2f}s)")
            return 0

        log.info(f"  {tabela} [RECNO]: {total_novos} novo(s) registro(s) a inserir...")

        cur_orig.execute(f"SELECT * FROM [dbo].[{tabela}] WITH(NOLOCK) WHERE R_E_C_N_O_ > ? ORDER BY R_E_C_N_O_", max_recno)

        n_colunas = len(cur_orig.description)
        placeholders = ','.join(['?' for _ in range(n_colunas)])
        insert_sql = f"INSERT INTO [dbo].[{tabela}] VALUES ({placeholders})"

        inseridos = 0
        cur_dest.fast_executemany = True

        while True:
            rows = cur_orig.fetchmany(batch_size)
            if not rows:
                break
            try:
                cur_dest.executemany(insert_sql, rows)
            except Exception as e:
                log.warning(f"  {tabela}: Falha com fast_executemany. Tentando gravação padrão... Detalhe: {e}")
                cur_dest.fast_executemany = False
                cur_dest.executemany(insert_sql, rows)
            conn_dest.commit()
            inseridos += len(rows)

    t_total = time.perf_counter() - t_inicio
    log.info(f"  {tabela}: finalizada com sucesso. Inseridos/Atualizados: {inseridos} (Tempo: {t_total:.2f}s)")
    return inseridos


# ===========================================================================
# COMPLETO — recarga total da tabela (swap: carrega numa tabela sombra e troca no final)
# ===========================================================================
SUFIXO_TABELA_SOMBRA = "__reload"
SUFIXO_TABELA_ANTIGA = "__old"


def completo_tabela(conn_orig: pyodbc.Connection, conn_dest: pyodbc.Connection,
                    tabela: str, batch_size: int, log: logging.Logger) -> int:
    """
    Recarrega a tabela inteira a partir da origem SEM truncar a tabela em uso.

    Em vez de TRUNCATE + INSERT na tabela viva (que exige lock exclusivo de schema e bloqueia
    — ou é bloqueado por — qualquer leitor da tabela, como planilhas/BI, por todo o tempo da
    carga), os dados são carregados numa tabela sombra (`tabela__reload`) que ninguém mais
    consulta. Só no final, já com os dados prontos, um `sp_rename` troca a tabela sombra pelo
    nome da tabela viva — operação de metadado, na casa de milissegundos, não minutos. Quem
    está lendo continua vendo a tabela antiga completa até o instante exato da troca.

    Como efeito colateral, isso também resolve a evolução de schema: a sombra sempre nasce com
    a estrutura atual da origem, então não é mais preciso comparar/dropar a tabela viva antes.
    """
    t_inicio = time.perf_counter()
    cur_dest = conn_dest.cursor()
    cur_orig = conn_orig.cursor()
    cur_orig.arraysize = batch_size

    colunas_origem = get_schema_tabela(conn_orig, tabela)
    if not colunas_origem:
        log.error(f"  {tabela}: tabela não encontrada na origem.")
        return 0

    tabela_sombra = f"{tabela}{SUFIXO_TABELA_SOMBRA}"
    tabela_antiga = f"{tabela}{SUFIXO_TABELA_ANTIGA}"

    # Limpa resíduo de uma execução anterior abortada no meio do caminho
    cur_dest.execute(f"IF OBJECT_ID('dbo.[{tabela_sombra}]', 'U') IS NOT NULL DROP TABLE dbo.[{tabela_sombra}]")
    cur_dest.execute(f"IF OBJECT_ID('dbo.[{tabela_antiga}]', 'U') IS NOT NULL DROP TABLE dbo.[{tabela_antiga}]")
    conn_dest.commit()

    criar_tabela(cur_dest, tabela_sombra, colunas_origem)
    conn_dest.commit()

    cur_orig.execute(f"SELECT COUNT(*) FROM [dbo].[{tabela}] WITH(NOLOCK)")
    total = cur_orig.fetchone()[0]
    log.info(f"  {tabela}: carregando {total} registro(s) em tabela sombra...")

    cur_orig.execute(f"SELECT * FROM [dbo].[{tabela}] WITH(NOLOCK)")

    n_colunas = len(cur_orig.description)
    placeholders = ','.join(['?' for _ in range(n_colunas)])
    insert_sql = f"INSERT INTO [dbo].[{tabela_sombra}] VALUES ({placeholders})"

    inseridos = 0
    cur_dest.fast_executemany = True
    input_sizes = montar_input_sizes_datetime(colunas_origem)
    if input_sizes is not None:
        try:
            cur_dest.setinputsizes(input_sizes)
        except Exception as e:
            log.warning(f"  {tabela}: setinputsizes falhou ({e}), desativando fast_executemany.")
            cur_dest.fast_executemany = False

    try:
        while True:
            rows = cur_orig.fetchmany(batch_size)
            if not rows:
                break
            try:
                cur_dest.executemany(insert_sql, rows)
            except Exception as e:
                log.warning(f"  {tabela}: Falha com executemany (fast_executemany={cur_dest.fast_executemany}). Detalhe: {e}")
                if cur_dest.fast_executemany:
                    # Uma segunda tentativa em modo lento cobre a maioria dos casos de incompatibilidade de tipo.
                    cur_dest.fast_executemany = False
                    try:
                        cur_dest.executemany(insert_sql, rows)
                    except Exception as e2:
                        log.warning(f"  {tabela}: Falha também em modo lento. Detalhe: {e2}")
                        for row in rows:
                            try:
                                cur_dest.execute(insert_sql, row)
                            except Exception as e3:
                                log.error(f"  {tabela}: Erro ao inserir linha individual: {e3}")
                else:
                    # Tenta linha por linha como último recurso
                    for row in rows:
                        try:
                            cur_dest.execute(insert_sql, row)
                        except Exception as e2:
                            log.error(f"  {tabela}: Erro ao inserir linha individual: {e2}")
            conn_dest.commit()
            inseridos += len(rows)

        # Troca atômica: a tabela viva vira "__old" e a sombra assume o nome dela.
        # Ambos os renames ficam na mesma transação — se o segundo falhar, o rollback
        # devolve a tabela original ao nome dela, sem deixar a tabela "sumida".
        log.info(f"  {tabela}: carga concluída, trocando tabela sombra pela tabela em uso...")
        if tabela_existe(conn_dest, tabela):
            cur_dest.execute(f"EXEC sp_rename 'dbo.[{tabela}]', '{tabela_antiga}'")
        cur_dest.execute(f"EXEC sp_rename 'dbo.[{tabela_sombra}]', '{tabela}'")
        # sp_rename troca só o nome da tabela — a constraint de PK criada em criar_tabela() continua
        # se chamando 'PK_{tabela_sombra}'; renomeia junto para não deixar um nome órfão no schema.
        cur_dest.execute(
            f"IF OBJECT_ID('dbo.PK_{tabela_sombra}', 'PK') IS NOT NULL "
            f"EXEC sp_rename 'dbo.PK_{tabela_sombra}', 'PK_{tabela}', 'OBJECT'"
        )
        conn_dest.commit()
    except Exception:
        conn_dest.rollback()
        log.exception(f"  {tabela}: falha durante a carga/troca, tabela sombra descartada — a tabela em uso não foi alterada.")
        try:
            cur_dest.execute(f"IF OBJECT_ID('dbo.[{tabela_sombra}]', 'U') IS NOT NULL DROP TABLE dbo.[{tabela_sombra}]")
            conn_dest.commit()
        except Exception:
            pass
        raise

    # A tabela antiga já foi rebatizada e ninguém mais aponta pra ela — descarte fora do
    # caminho crítico (se falhar aqui, só sobra uma tabela órfã, sem risco pros dados).
    try:
        cur_dest.execute(f"IF OBJECT_ID('dbo.[{tabela_antiga}]', 'U') IS NOT NULL DROP TABLE dbo.[{tabela_antiga}]")
        conn_dest.commit()
    except Exception:
        log.warning(f"  {tabela}: não foi possível remover a tabela antiga '{tabela_antiga}', limpar manualmente depois.")

    t_total = time.perf_counter() - t_inicio
    log.info(f"  {tabela}: finalizada com sucesso. Carregados: {inseridos} (Tempo: {t_total:.2f}s)")
    return inseridos


# ===========================================================================
# RECONCILIAÇÃO DE EXCLUSÕES — remove no destino o que foi apagado fisicamente na origem
# ===========================================================================
def reconciliar_exclusoes(conn_orig: pyodbc.Connection, conn_dest: pyodbc.Connection,
                          tabela: str, schema_orig: str, schema_dest: str,
                          log: logging.Logger, limite_seguranca_pct: float = 0.05,
                          chave: str = 'R_E_C_N_O_') -> int:
    """
    Remove do destino os registros cuja chave não existe mais na origem.

    O DIFF (UPSERT por S_T_A_M_P_) nunca apaga nada no destino — só insere/atualiza. Se um
    registro é apagado fisicamente na origem (não só marcado via D_E_L_E_T_), o destino nunca
    fica sabendo e a tabela diverge para sempre, até alguém rodar uma recarga completa. Esta
    função resolve isso sem tocar na tabela inteira: traz só a coluna-chave (leve mesmo em
    tabelas com milhões de linhas) dos dois lados, calcula a diferença em Python e apaga
    pontualmente só os registros "órfãos" no destino.

    Por segurança, se a proporção de órfãos for maior que `limite_seguranca_pct` do total do
    destino, aborta sem apagar nada — protege contra uma leitura parcial/instável da origem
    (ex.: queda de rede no meio da consulta) sendo interpretada como exclusão em massa. Nesse
    caso a divergência segue para a recarga completa normalmente.
    """
    cur_orig = conn_orig.cursor()
    cur_dest = conn_dest.cursor()

    cur_orig.execute(f"SELECT [{chave}] FROM [{schema_orig}].[{tabela}] WITH(NOLOCK)")
    recnos_origem = {row[0] for row in cur_orig.fetchall()}

    cur_dest.execute(f"SELECT [{chave}] FROM [{schema_dest}].[{tabela}]")
    recnos_destino = {row[0] for row in cur_dest.fetchall()}

    orfaos = recnos_destino - recnos_origem
    if not orfaos:
        return 0

    if recnos_destino and (len(orfaos) / len(recnos_destino)) > limite_seguranca_pct:
        log.warning(
            f"  {tabela}: {len(orfaos)} registro(s) órfão(s) detectado(s) "
            f"({len(orfaos) / len(recnos_destino):.1%} do destino, acima do limite de segurança de "
            f"{limite_seguranca_pct:.0%}). Abortando reconciliação de exclusões; segue para recarga completa."
        )
        return 0

    orfaos_lista = list(orfaos)
    removidos = 0
    tamanho_lote = 1000
    for i in range(0, len(orfaos_lista), tamanho_lote):
        lote = orfaos_lista[i:i + tamanho_lote]
        placeholders = ','.join(['?'] * len(lote))
        cur_dest.execute(f"DELETE FROM [{schema_dest}].[{tabela}] WHERE [{chave}] IN ({placeholders})", lote)
        conn_dest.commit()
        removidos += len(lote)

    log.info(f"  {tabela}: {removidos} registro(s) removido(s) por exclusão física detectada na origem.")
    return removidos


# ===========================================================================
# EXECUÇÃO PRINCIPAL
# ===========================================================================
# ===========================================================================
# WORKER PARALELO
# ===========================================================================
def worker_replicar_tabela(tabela: str, modo: str, cfg: configparser.ConfigParser, batch_size: int) -> tuple[str, int, str | None]:
    """
    Worker executado em thread separada. Abre suas próprias conexões e replica a tabela.
    Recria as conexões em caso de retry por perda de link.
    """
    logger = logging.getLogger('replicacao')
    
    def tentar_replicar():
        conn_orig = None
        conn_dest = None
        try:
            conn_orig = get_conexao_com_retry(cfg, 'origem')
            conn_dest = get_conexao_com_retry(cfg, 'destino')
            fn = completo_tabela if modo == 'completo' else diff_tabela
            return fn(conn_orig, conn_dest, tabela, batch_size, logger)
        finally:
            if conn_orig:
                try: conn_orig.close()
                except Exception: pass
            if conn_dest:
                try: conn_dest.close()
                except Exception: pass

    try:
        max_tentativas = int(cfg['configuracao'].get('dias_retry_attempts', '3'))
        n = executar_com_retry(tentar_replicar, max_tentativas=max_tentativas, espera_inicial=3)
        return (tabela, n, None)
    except Exception as e:
        return (tabela, 0, str(e))


# ===========================================================================
# LIMPEZA DE LOGS
# ===========================================================================
def limpar_logs_antigos(log_dir: str | Path, dias_retencao: int, log: logging.Logger) -> None:
    """Remove arquivos de log mais antigos que o período de retenção especificado."""
    try:
        limite_tempo = time.time() - (dias_retencao * 86400)
        caminho_dir = Path(log_dir)
        if not caminho_dir.exists():
            return
            
        removidos = 0
        for arquivo in caminho_dir.rglob("*.log"):
            if arquivo.is_file() and arquivo.stat().st_mtime < limite_tempo:
                try:
                    arquivo.unlink()
                    removidos += 1
                except Exception as e:
                    log.warning(f"Não foi possível remover o log antigo {arquivo.name}: {e}")
                    
        if removidos > 0:
            log.info(f"Limpeza de logs: {removidos} arquivo(s) de log com mais de {dias_retencao} dias removidos.")
    except Exception as e:
        log.error(f"Erro ao executar limpeza de logs antigos: {e}")


# ===========================================================================
# VALIDAÇÃO INTELIGENTE DE DIVERGÊNCIAS
# ===========================================================================
def obter_tabelas_divergentes(cfg: configparser.ConfigParser, tabelas: list[str], log: logging.Logger) -> list[str]:
    """
    Compara a contagem de registros e deleções lógicas entre a origem e o destino.
    Retorna a lista de tabelas que possuem divergências ou não existem.
    """
    log.info("Iniciando validação inteligente de integridade para identificar divergências...")
    divergentes = []
    
    conn_orig = None
    conn_dest = None
    try:
        conn_orig = get_conexao_com_retry(cfg, 'origem')
        conn_dest = get_conexao_com_retry(cfg, 'destino')
        
        schema_orig = cfg['origem'].get('schema', 'dbo')
        schema_dest = cfg['destino'].get('schema', 'dbo')
        
        for tabela in tabelas:
            # 1. Verificar existência no destino
            if not tabela_existe(conn_dest, tabela):
                log.warning(f"  {tabela}: não existe no destino. Será recarregada.")
                divergentes.append(tabela)
                continue
                
            # 2. Verificar se a estrutura mudou (Schema Evolution)
            col_orig = get_schema_tabela(conn_orig, tabela)
            col_dest = get_schema_tabela(conn_dest, tabela)
            if not comparar_schemas(col_orig, col_dest):
                log.warning(f"  {tabela}: estrutura divergente detectada. Será recarregada.")
                divergentes.append(tabela)
                continue
                
            # 3. Comparar contagens de registros e deletados
            nomes_colunas = {c['nome'].upper() for c in col_orig}
            tem_delete = 'D_E_L_E_T_' in nomes_colunas
            
            # Query na origem
            cur_orig = conn_orig.cursor()
            q_orig = f"SELECT COUNT(*), SUM(CASE WHEN RTRIM(D_E_L_E_T_) <> '' THEN 1 ELSE 0 END) FROM [{schema_orig}].[{tabela}] WITH(NOLOCK)" if tem_delete else f"SELECT COUNT(*), 0 FROM [{schema_orig}].[{tabela}] WITH(NOLOCK)"
            cur_orig.execute(q_orig)
            res_orig = cur_orig.fetchone()
            count_orig = res_orig[0] or 0
            del_orig = res_orig[1] or 0
            
            # Query no destino
            cur_dest = conn_dest.cursor()
            q_dest = f"SELECT COUNT(*), SUM(CASE WHEN RTRIM(D_E_L_E_T_) <> '' THEN 1 ELSE 0 END) FROM [{schema_dest}].[{tabela}] WITH(NOLOCK)" if tem_delete else f"SELECT COUNT(*), 0 FROM [{schema_dest}].[{tabela}] WITH(NOLOCK)"
            cur_dest.execute(q_dest)
            res_dest = cur_dest.fetchone()
            count_dest = res_dest[0] or 0
            del_dest = res_dest[1] or 0
            
            if count_orig != count_dest or del_orig != del_dest:
                log.warning(f"  {tabela}: DIVERGENTE (Origem: {count_orig} reg, {del_orig} del | Destino: {count_dest} reg, {del_dest} del). Será recarregada.")
                divergentes.append(tabela)
            
    except Exception as e:
        log.error(f"Erro durante a identificação de divergências: {e}")
        # Em caso de erro na validação, assume todas como divergentes por segurança
        return tabelas
    finally:
        if conn_orig: conn_orig.close()
        if conn_dest: conn_dest.close()
        
    return divergentes


# ===========================================================================
# EXECUÇÃO PRINCIPAL
# ===========================================================================
def executar(modo: str, tabela_unica: str | None, cfg: configparser.ConfigParser,
             log: logging.Logger, smart: bool = False) -> bool:
    """Ponto de entrada público: adquire o lock de execução (impede duas instâncias rodando ao
    mesmo tempo, não importa a via de disparo) e delega para _executar_interno.
    Levanta ExecucaoBloqueadaError se outra execução já estiver em andamento."""
    lock_conn = adquirir_lock_execucao(cfg)
    if lock_conn is None:
        log.warning(
            "Já existe outra execução de replicação em andamento (lock de execução ocupado). "
            "Esta execução será ignorada para evitar duplicidade de registros no destino."
        )
        raise ExecucaoBloqueadaError("Lock de execução ocupado por outra instância de replicacao.py.")

    try:
        return _executar_interno(modo, tabela_unica, cfg, log, smart=smart)
    finally:
        liberar_lock_execucao(lock_conn)


def _executar_interno(modo: str, tabela_unica: str | None, cfg: configparser.ConfigParser,
                       log: logging.Logger, smart: bool = False) -> bool:
    batch_size = int(cfg['configuracao'].get('batch_size', '5000'))
    
    # Carrega listas dinamicamente com fallback para as constantes default
    tabelas_diff = carregar_lista_tabelas(cfg, 'tabelas_diff', TABELAS_DIFF_DEFAULT)
    tabelas_completo = carregar_lista_tabelas(cfg, 'tabelas_completo', TABELAS_COMPLETO_DEFAULT)
    tabelas = tabelas_completo if modo == 'completo' else tabelas_diff
    tabela_alvo_historico = tabela_unica  # registrado no histórico (REPLICACAO_EXECUCOES) tal como veio do chamador

    if tabela_unica:
        tabelas_validas = sorted(set(tabelas_diff + tabelas_completo))
        lista_filtro = [t.strip().upper() for t in tabela_unica.split(',') if t.strip()]
        tabelas = [t for t in tabelas_validas if t.upper() in lista_filtro]
        if not tabelas:
            log.error(f"Nenhuma das tabelas especificada(s) '{tabela_unica}' foi encontrada no mapeamento.")
            sys.exit(1)
    elif modo == 'completo' and smart:
        tabelas = obter_tabelas_divergentes(cfg, tabelas, log)
        if not tabelas:
            log.info(f"{'='*60}")
            log.info("Modo: COMPLETO (SMART) | Nenhuma divergência encontrada. Tudo em sincronia!")
            log.info(f"{'='*60}")
            execucao_id = db_historico.registrar_inicio_execucao(cfg, modo, tabela_alvo=tabela_alvo_historico, total_tabelas=0, smart=smart)
            db_historico.registrar_fim_execucao(cfg, execucao_id, status='SUCESSO', tabelas_atualizadas=0, registros_inseridos=0)
            return True

    log.info(f"{'='*60}")
    log.info(f"Modo: {modo.upper()} | Tabelas: {len(tabelas)} | Batch: {batch_size}")
    log.info(f"{'='*60}")

    execucao_id = db_historico.registrar_inicio_execucao(cfg, modo, tabela_alvo=tabela_alvo_historico, total_tabelas=len(tabelas), smart=smart)

    inicio = datetime.now()
    max_workers = int(cfg['configuracao'].get('max_workers', '4'))

    total_inseridos = 0
    erros = []
    detalhes_tabelas = []  # (tabela, registros, status) — histórico estruturado, ver REPLICACAO_EXECUCOES_TABELAS

    if max_workers > 1 and len(tabelas) > 1:
        log.info(f"Executando com ThreadPoolExecutor ({max_workers} workers)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(worker_replicar_tabela, tabela, modo, cfg, batch_size): tabela
                for tabela in tabelas
            }
            for future in concurrent.futures.as_completed(futures):
                tabela = futures[future]
                try:
                    tab, n, err = future.result()
                    if err:
                        log.error(f"  {tabela}: ERRO — {err}")
                        erros.append(tabela)
                        detalhes_tabelas.append((tabela, 0, 'ERRO'))
                    else:
                        total_inseridos += n
                        detalhes_tabelas.append((tabela, n, 'SUCESSO'))
                except Exception as e:
                    log.error(f"  {tabela}: Exceção no worker — {e}")
                    erros.append(tabela)
                    detalhes_tabelas.append((tabela, 0, 'ERRO'))
    else:
        log.info("Executando de forma sequencial (max_workers = 1)...")
        fn = completo_tabela if modo == 'completo' else diff_tabela
        max_tentativas = int(cfg['configuracao'].get('dias_retry_attempts', '3'))

        for tabela in tabelas:
            def tentar_replicar_seq():
                c_orig = None
                c_dest = None
                try:
                    c_orig = get_conexao_com_retry(cfg, 'origem')
                    c_dest = get_conexao_com_retry(cfg, 'destino')
                    return fn(c_orig, c_dest, tabela, batch_size, log)
                finally:
                    if c_orig:
                        try: c_orig.close()
                        except Exception: pass
                    if c_dest:
                        try: c_dest.close()
                        except Exception: pass
            try:
                n = executar_com_retry(tentar_replicar_seq, max_tentativas=max_tentativas, espera_inicial=3)
                total_inseridos += n
                detalhes_tabelas.append((tabela, n, 'SUCESSO'))
            except Exception as e:
                log.error(f"  {tabela}: ERRO — {e}")
                erros.append(tabela)
                detalhes_tabelas.append((tabela, 0, 'ERRO'))

    duracao = datetime.now() - inicio
    log.info(f"{'='*60}")
    log.info(f"Concluído em {duracao} | Inseridos/Atualizados: {total_inseridos} | Erros: {len(erros)}")
    if erros:
        log.warning(f"Tabelas com erro: {', '.join(erros)}")
    log.info(f"{'='*60}")

    db_historico.registrar_fim_execucao(
        cfg, execucao_id,
        status='FALHA' if erros else 'SUCESSO',
        tabelas_atualizadas=len(tabelas) - len(erros),
        registros_inseridos=total_inseridos,
        erro='; '.join(erros) if erros else None,
        detalhes_tabelas=detalhes_tabelas,
    )

    if erros:
        return False
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Replicação Protheus BI')
    parser.add_argument('--modo', choices=['diff', 'completo'], required=True)
    parser.add_argument('--tabela', default=None)
    parser.add_argument('--config', default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument('--smart', action='store_true', help='No modo completo, recarrega apenas tabelas divergentes')
    args = parser.parse_args()

    cfg = configparser.ConfigParser()
    config_path = resolver_caminho(args.config, DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        print(f"Arquivo de configuração não encontrado: {config_path}", file=sys.stderr)
        sys.exit(1)
    # utf-8-sig: tolera (e remove) o BOM que Notepad/PowerShell costumam gravar ao salvar
    # o config.ini como UTF-8 no Windows — com 'utf-8' puro isso quebra o parser do INI.
    cfg.read(config_path, encoding='utf-8-sig')

    log_dir = cfg['configuracao'].get('log_dir', 'logs')
    log, log_path = configurar_log(log_dir, args.modo)

    try:
        sucesso = executar(args.modo, args.tabela, cfg, log, smart=args.smart)
    except ExecucaoBloqueadaError as e:
        # Outra execução já está rodando — não é uma falha, é o lock funcionando como esperado.
        # Sai sem enviar e-mail/rodar speedtest para não gerar ruído a cada ciclo de 5 minutos.
        log.warning(f"Execução ignorada: {e}")
        sys.exit(0)

    # Executa a limpeza automática dos logs antigos se configurado
    dias_retencao = int(cfg['configuracao'].get('dias_retencao_logs', '14'))
    limpar_logs_antigos(log_dir, dias_retencao, log)

    # Mesma retenção pro histórico estruturado (REPLICACAO_EXECUCOES) — Fase 1 do roadmap v2.
    removidos = db_historico.limpar_historico_antigo(cfg, dias_retencao)
    if removidos:
        log.info(f"Limpeza de histórico: {removidos} execução(ões) com mais de {dias_retencao} dias removida(s).")

    # ===========================================================================
    # TESTE DE VELOCIDADE DE INTERNET (só no modo DIFF — a tarefa roda a cada 5 min, ver
    # docs/instalar_tarefas.ps1, e não faz sentido medir a cada execução, então só roda de novo
    # se a última medição salva já não for recente)
    # ===========================================================================
    if args.modo == 'diff' and not speedtest_servico.resultado_esta_recente(minutos=30):
        try:
            log.info("Medindo velocidade de internet do servidor...")
            resultado_speed = speedtest_servico.executar_speedtest(origem='automatico_diff')
            speedtest_servico.salvar_resultado(resultado_speed)
            if resultado_speed.get("error"):
                log.warning(f"[AVISO] Teste de velocidade falhou: {resultado_speed['error']}")
            else:
                log.info(
                    f"Velocidade: download {resultado_speed['download_mbps']} Mbps | "
                    f"upload {resultado_speed['upload_mbps']} Mbps | ping {resultado_speed['ping_ms']} ms"
                )
        except Exception as e:
            log.warning(f"[AVISO] Falha ao rodar teste de velocidade: {e}")

    # ===========================================================================
    # ENVIO DE NOTIFICAÇÃO POR E-MAIL
    # ===========================================================================
    if EMAIL_DISPONIVEL:
        try:
            # Aguarda um pouco para garantir que o arquivo de log foi escrito completamente
            import time as time_module
            time_module.sleep(1)
            
            log.info("Enviando notificação por e-mail...")
            relatorio = RelatorioReplicacao(log_path)
            notificador = NotificadorEmail(cfg, log)
            
            if notificador.enviar(relatorio):
                log.info("[OK] Notificação por e-mail enviada com sucesso.")
            else:
                log.warning("[AVISO] Falha ao enviar notificação por e-mail (verifique configurações).")
        except Exception as e:
            import traceback
            log.error(f"[ERRO] Erro ao processar notificação por e-mail: {e}")
            log.error(f"Traceback completo:\n{traceback.format_exc()}")
    else:
        log.warning("[AVISO] Módulo de e-mail não disponível. Instale 'email_notificacao.py' no mesmo diretório.")
        
    if not sucesso:
        sys.exit(1)
