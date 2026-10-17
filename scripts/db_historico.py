"""
Persistência estruturada do histórico de execuções — Fase 1 do roadmap v2
(ver docs/ROADMAP_V2.md).

Grava no mesmo SQL Server de destino (BI) já usado pela replicação, em
tabelas separadas (REPLICACAO_EXECUCOES / REPLICACAO_EXECUCOES_TABELAS —
DDL em db/historico-execucoes.sql). O objetivo é eliminar, aos
poucos, o parsing de arquivos .log que hoje sustenta taxa de sucesso,
progresso e histórico no painel.

Toda função de escrita aqui é best-effort: se o histórico falhar (banco
fora do ar, tabela ainda não criada, etc.) o erro é só logado — nunca deve
derrubar a replicação em si, que é o que realmente importa.
"""

import configparser
import logging
import os
from datetime import datetime

import pyodbc

logger = logging.getLogger('db_historico')


def _get_conexao(cfg: configparser.ConfigParser) -> pyodbc.Connection:
    """Conecta no banco de destino (BI). Mesma receita de string de conexão usada em
    replicacao.py/validador_integridade.py, mas com autocommit=True: cada gravação de
    histórico é uma operação isolada e curta, sem necessidade (nem vantagem) de
    coordenar com a transação da replicação em si."""
    s = cfg['destino'] if 'destino' in cfg else {}
    servidor = os.environ.get('DB_DESTINO_SERVER') or s.get('server', '')
    porta = os.environ.get('DB_DESTINO_PORT') or s.get('port', '1433')
    database = os.environ.get('DB_DESTINO_DB') or s.get('database', '')
    username = os.environ.get('DB_DESTINO_USER') or s.get('username', '')
    password = os.environ.get('DB_DESTINO_PASS') or s.get('password', '')
    driver = os.environ.get('DB_DESTINO_DRIVER') or s.get('driver', 'ODBC Driver 17 for SQL Server')
    timeout_conexao = cfg['configuracao'].get('timeout_conexao', '30') if 'configuracao' in cfg else '30'

    server_str = servidor if (',' in servidor or '\\' in servidor) else f"{servidor},{porta}"
    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server_str};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        f"TrustServerCertificate=yes;"
        f"Encrypt=yes;"
        f"Connection Timeout={timeout_conexao};"
    )
    return pyodbc.connect(conn_str, autocommit=True)


def registrar_inicio_execucao(cfg, modo, tabela_alvo=None, total_tabelas=None, smart=False):
    """Insere a linha de início de uma execução e devolve o ID gerado (None em caso de falha).

    `tabela_alvo` é a lista de tabelas (separadas por vírgula) quando a carga foi disparada
    para tabela(s) específica(s) via painel; None quando é a carga padrão completa/diff.
    """
    try:
        conn = _get_conexao(cfg)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO REPLICACAO_EXECUCOES
                    (MODO, SMART, TABELA_ALVO, STATUS, DATA_INICIO, TOTAL_TABELAS)
                OUTPUT INSERTED.ID
                VALUES (?, ?, ?, 'RODANDO', ?, ?)
                """,
                modo, 1 if smart else 0, tabela_alvo, datetime.now(), total_tabelas
            )
            return cur.fetchone()[0]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Falha ao registrar início de execução no histórico (modo={modo}): {e}")
        return None


def registrar_fim_execucao(cfg, execucao_id, status, tabelas_atualizadas=None,
                            registros_inseridos=None, erro=None, detalhes_tabelas=None):
    """Fecha a linha de execução (status/duração/totais) e, se fornecido, grava o detalhe
    por tabela em lote (uma única conexão, um único executemany — não uma conexão por tabela).

    `detalhes_tabelas`: lista de tuplas (tabela, registros, status).
    No-op silencioso se `execucao_id` for None (registro de início já tinha falhado).
    """
    if execucao_id is None:
        return
    try:
        conn = _get_conexao(cfg)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE REPLICACAO_EXECUCOES
                SET STATUS = ?, DATA_FIM = SYSDATETIME(),
                    DURACAO_SEG = DATEDIFF(SECOND, DATA_INICIO, SYSDATETIME()),
                    TABELAS_ATUALIZADAS = ?, REGISTROS_INSERIDOS = ?, ERRO = ?
                WHERE ID = ?
                """,
                status, tabelas_atualizadas, registros_inseridos, erro, execucao_id
            )
            if detalhes_tabelas:
                cur.executemany(
                    """
                    INSERT INTO REPLICACAO_EXECUCOES_TABELAS (EXECUCAO_ID, TABELA, REGISTROS, STATUS)
                    VALUES (?, ?, ?, ?)
                    """,
                    [(execucao_id, tabela, registros, status_tabela) for tabela, registros, status_tabela in detalhes_tabelas]
                )
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Falha ao registrar fim de execução {execucao_id} no histórico: {e}")


def limpar_historico_antigo(cfg, dias_retencao):
    """Remove do histórico execuções (e o detalhe por tabela associado) com mais de
    `dias_retencao` dias. Mesmo propósito de `limpar_logs_antigos` (replicacao.py) pros
    arquivos .log, mas aqui é um DELETE indexado em vez de varrer o sistema de arquivos.

    Sem ON DELETE CASCADE no FK de propósito (mais explícito) — apaga primeiro o detalhe
    por tabela, depois a execução. Retorna quantas execuções foram removidas (0 em caso
    de falha, sem lançar — retenção não pode derrubar a replicação em si).
    """
    try:
        conn = _get_conexao(cfg)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                DELETE FROM REPLICACAO_EXECUCOES_TABELAS
                WHERE EXECUCAO_ID IN (
                    SELECT ID FROM REPLICACAO_EXECUCOES WHERE DATA_INICIO < DATEADD(DAY, -?, SYSDATETIME())
                )
                """,
                dias_retencao
            )
            cur.execute(
                "DELETE FROM REPLICACAO_EXECUCOES WHERE DATA_INICIO < DATEADD(DAY, -?, SYSDATETIME())",
                dias_retencao
            )
            return cur.rowcount
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Falha ao limpar histórico antigo (retenção {dias_retencao} dias): {e}")
        return 0


def consultar_ultima_execucao(cfg, modo):
    """Retorna a linha mais recente de REPLICACAO_EXECUCOES para o modo, como dict (ou None)."""
    try:
        conn = _get_conexao(cfg)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT TOP 1 ID, MODO, SMART, TABELA_ALVO, STATUS, DATA_INICIO, DATA_FIM,
                       DURACAO_SEG, TOTAL_TABELAS, TABELAS_ATUALIZADAS, REGISTROS_INSERIDOS, ERRO
                FROM REPLICACAO_EXECUCOES
                WHERE MODO = ?
                ORDER BY DATA_INICIO DESC
                """,
                modo
            )
            row = cur.fetchone()
            if not row:
                return None
            colunas = [c[0] for c in cur.description]
            return dict(zip(colunas, row))
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Falha ao consultar última execução (modo={modo}) no histórico: {e}")
        return None


def consultar_taxa_sucesso(cfg, modo, dias=7):
    """Retorna {"total", "sucesso", "percentual"} das execuções concluídas (não RODANDO)
    do modo nos últimos `dias`."""
    try:
        conn = _get_conexao(cfg)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*), SUM(CASE WHEN STATUS = 'SUCESSO' THEN 1 ELSE 0 END)
                FROM REPLICACAO_EXECUCOES
                WHERE MODO = ? AND STATUS <> 'RODANDO' AND DATA_INICIO >= DATEADD(DAY, -?, SYSDATETIME())
                """,
                modo, dias
            )
            total, sucesso = cur.fetchone()
            total = total or 0
            sucesso = sucesso or 0
            percentual = round((sucesso / total) * 100, 1) if total > 0 else None
            return {"total": total, "sucesso": sucesso, "percentual": percentual}
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Falha ao consultar taxa de sucesso (modo={modo}) no histórico: {e}")
        return {"total": 0, "sucesso": 0, "percentual": None}


def consultar_historico(cfg, modo, limite=200):
    """Retorna as execuções mais recentes do modo (mais nova primeiro), como lista de dicts."""
    try:
        conn = _get_conexao(cfg)
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT TOP {int(limite)} ID, MODO, SMART, TABELA_ALVO, STATUS, DATA_INICIO, DATA_FIM,
                       DURACAO_SEG, TOTAL_TABELAS, TABELAS_ATUALIZADAS, REGISTROS_INSERIDOS, ERRO
                FROM REPLICACAO_EXECUCOES
                WHERE MODO = ?
                ORDER BY DATA_INICIO DESC
                """,
                modo
            )
            colunas = [c[0] for c in cur.description]
            return [dict(zip(colunas, row)) for row in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Falha ao consultar histórico (modo={modo}): {e}")
        return []

