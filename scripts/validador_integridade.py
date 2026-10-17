"""
Validador de integridade entre os bancos de origem e destino.
Executa o SQL de validação e envia um e-mail resumido com o status.
"""

import concurrent.futures
import configparser
import json
import logging
import smtplib
import sys
import os
import argparse
import threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pyodbc

import db_historico

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / 'config' / 'config.ini'
RESUMO_VALIDACAO_PATH = ROOT_DIR / 'logs' / 'validacao' / 'ultimo_resultado.json'

# Reaproveita as rotinas de correção do job das 02:00 para corrigir automaticamente
# divergências encontradas às 06:00: reconciliar_exclusoes trata exclusões físicas na origem
# sem tocar na tabela inteira; completo_tabela (recarga via swap de tabela, sem truncar a
# tabela em uso) é o fallback para o que sobrar (schema mudou, tabela nova, etc.).
#
# adquirir_lock_execucao/liberar_lock_execucao: mesmo lock de execução (sp_getapplock) que o
# replicacao.py usa em executar() — sem ele, a correção automática daqui (completo_tabela,
# remover_duplicados) escreve no destino sem coordenação com um DIFF/COMPLETO rodando ao mesmo
# tempo, o que já causou duplicidade nova em produção.
try:
    from replicacao import completo_tabela, reconciliar_exclusoes, adquirir_lock_execucao, liberar_lock_execucao
    CORRECAO_AUTOMATICA_DISPONIVEL = True
except Exception:
    CORRECAO_AUTOMATICA_DISPONIVEL = False

# Sem lista de cliente embutida: configure as tabelas antes de executar.
TABELAS_VALIDACAO_DEFAULT: list[str] = []


def carregar_lista_tabelas(cfg: configparser.ConfigParser, opcao: str, padrao: list[str]) -> list[str]:
    """Lê a lista de tabelas do config.ini (seção [tabelas]), com fallback para a lista padrão."""
    if cfg.has_section('tabelas') and cfg.has_option('tabelas', opcao):
        val = cfg.get('tabelas', opcao).strip()
        if val:
            return [t.strip().upper() for t in val.split(',') if t.strip()]
    return padrao

def resolver_caminho(caminho: str | Path | None, fallback: Path | None = None) -> Path:
    if caminho is None:
        return fallback if fallback is not None else ROOT_DIR
    path = Path(caminho)
    if path.is_absolute():
        return path
    candidatos = [Path.cwd() / path, ROOT_DIR / path]
    for candidato in candidatos:
        if candidato.exists():
            return candidato.resolve()
    return (fallback if fallback is not None else (ROOT_DIR / path)).resolve()

def carregar_config(caminho: str | Path | None = None) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    config_path = resolver_caminho(caminho or DEFAULT_CONFIG_PATH, DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        raise FileNotFoundError(f'Arquivo de configuração não encontrado: {config_path}')
    # utf-8-sig: tolera (e remove) o BOM que Notepad/PowerShell costumam gravar ao salvar
    # o config.ini como UTF-8 no Windows — com 'utf-8' puro isso quebra o parser do INI.
    cfg.read(config_path, encoding='utf-8-sig')
    return cfg

def get_conexao(cfg: configparser.ConfigParser, secao: str) -> pyodbc.Connection:
    s = cfg[secao] if secao in cfg else {}
    
    # Fallback para variáveis de ambiente
    prefixo = 'DB_ORIGEM_' if secao == 'origem' else 'DB_DESTINO_'
    servidor = os.environ.get(prefixo + 'SERVER') or s.get('server', '')
    porta = os.environ.get(prefixo + 'PORT') or s.get('port', '1433')
    database = os.environ.get(prefixo + 'DB') or s.get('database', '')
    uid = os.environ.get(prefixo + 'USER') or s.get('username', '')
    pwd = os.environ.get(prefixo + 'PASS') or s.get('password', '')
    driver = os.environ.get(prefixo + 'DRIVER') or s.get('driver', 'ODBC Driver 17 for SQL Server')
    
    # Validação padrão de schema para evitar ambiguidade (default 'dbo')
    s.get('schema', 'dbo') 

    if ',' in servidor or '\\' in servidor:
        server_str = servidor
    else:
        server_str = f"{servidor},{porta}"

    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server_str};"
        f"DATABASE={database};"
        f"UID={uid};"
        f"PWD={pwd};"
        f"TrustServerCertificate=yes;"
        f"Encrypt=yes;"
    )
    return pyodbc.connect(conn_str)


def tabela_existe(conn, schema, tabela):
    cur = conn.cursor()
    # CORREÇÃO: Filtrando por SCHEMA para evitar conflitos se a tabela existir em múltiplos lugares
    cur.execute("""
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
    """, (schema, tabela))
    return cur.fetchone()[0] > 0

def obter_colunas(conn, schema, tabela):
    cur = conn.cursor()
    # CORREÇÃO: Filtrando por SCHEMA aqui também
    cur.execute("""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
    """, (schema, tabela))
    return {r[0].upper() for r in cur.fetchall()}

def obter_metricas(conn, schema, tabela):
    colunas = obter_colunas(conn, schema, tabela)
    tem_stamp = 'S_T_A_M_P_' in colunas
    tem_delete = 'D_E_L_E_T_' in colunas

    cur = conn.cursor()
    partes = ["COUNT(*) AS REGISTROS"]

    if tem_stamp and tem_delete:
        partes.append("""
            MAX(CASE WHEN RTRIM(D_E_L_E_T_) = '' THEN S_T_A_M_P_ END) AS ULTIMA_ATUALIZACAO
        """)
    elif tem_stamp:
        partes.append("MAX(S_T_A_M_P_) AS ULTIMA_ATUALIZACAO")
    else:
        partes.append("CAST(NULL AS DATETIME) AS ULTIMA_ATUALIZACAO")

    if tem_delete:
        partes.append("""
            SUM(CASE WHEN RTRIM(D_E_L_E_T_) <> '' THEN 1 ELSE 0 END) AS DELETADOS
        """)
    else:
        partes.append("0 AS DELETADOS")

    # CORREÇÃO: Adicionado o prefixo do schema na busca da tabela
    sql = f"SELECT {','.join(partes)} FROM [{schema}].[{tabela}] WITH(NOLOCK)"
    cur.execute(sql)
    row = cur.fetchone()

    return {
        "REGISTROS": row[0] if row[0] is not None else 0,
        "ULTIMA_ATUALIZACAO": row[1],
        "DELETADOS": row[2] if row[2] is not None else 0
    }

def contar_duplicados(conn, schema, tabela, chave='R_E_C_N_O_'):
    """Conta quantos registros 'excedentes' existem para a chave informada (linhas que seriam removidas em uma deduplicação)."""
    cur = conn.cursor()
    sql = f"""
        SELECT ISNULL(SUM(cnt - 1), 0)
        FROM (
            SELECT COUNT(*) AS cnt
            FROM [{schema}].[{tabela}] WITH (NOLOCK)
            GROUP BY [{chave}]
            HAVING COUNT(*) > 1
        ) dup
    """
    cur.execute(sql)
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def registrar_log_duplicados(tabela, schema, colunas, linhas):
    """Grava um log de auditoria com as linhas duplicadas removidas automaticamente."""
    try:
        log_dir = ROOT_DIR / 'logs' / 'duplicados'
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"duplicados_{tabela}_{datetime.now():%Y%m%d_%H%M%S}.log"
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(f"Tabela: {tabela}\n")
            f.write(f"Schema (BI): {schema}\n")
            f.write("Origem: validador_integridade.py (job automático 06:00)\n")
            f.write(f"Data/Hora da exclusão: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f.write("Chave de deduplicação: R_E_C_N_O_ (mantida a primeira ocorrência, excedentes removidos)\n")
            f.write(f"Total de registros duplicados removidos: {len(linhas)}\n")
            f.write("-" * 100 + "\n")
            if colunas:
                f.write(" | ".join(colunas) + "\n")
                f.write("-" * 100 + "\n")
                for linha in linhas:
                    f.write(" | ".join(str(v) for v in linha) + "\n")
    except Exception:
        logging.exception("Falha ao gravar log de duplicados removidos para %s", tabela)


def remover_duplicados(conn, schema, tabela, chave='R_E_C_N_O_'):
    """Remove registros duplicados de uma tabela, mantendo apenas 1 ocorrência por valor da chave.
    Retorna (colunas, linhas) com os dados das linhas efetivamente removidas, para fins de log/auditoria."""
    cur = conn.cursor()
    sql = f"""
        ;WITH dedup AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY [{chave}] ORDER BY (SELECT NULL)) AS __rn__
            FROM [{schema}].[{tabela}]
        )
        DELETE FROM dedup
        OUTPUT DELETED.*
        WHERE __rn__ > 1
    """
    cur.execute(sql)
    colunas = [c[0] for c in cur.description] if cur.description else []
    linhas = cur.fetchall()
    conn.commit()
    return colunas, linhas


def _validar_uma_tabela(cfg, tabela: str, schema_orig: str, schema_dest: str,
                         batch_size: int, pode_corrigir: bool,
                         logger_replicacao,
                         limite_linhas_tabela_grande: int,
                         semaforo_recarga_grande: threading.Semaphore,
                         limite_seguranca_exclusoes_pct: float) -> dict:
    """Valida (e opcionalmente corrige) uma única tabela. Abre suas próprias conexões
    para poder ser chamada em paralelo por múltiplas threads — conexões pyodbc não
    podem ser compartilhadas entre threads."""
    conn_orig = None
    conn_dest = None
    duplicados_removidos = 0
    correcao_aplicada = False
    try:
        conn_orig = get_conexao(cfg, "origem")
        conn_dest = get_conexao(cfg, "destino")

        existe_prod = tabela_existe(conn_orig, schema_orig, tabela)
        if not existe_prod:
            return {
                "TABELA": tabela,
                "REG_PRODUCAO": None, "REG_BI": None,
                "ULTIMA_ATUALIZACAO_PROD": None, "ULTIMA_ATUALIZACAO_BI": None,
                "DELETADOS_PROD": None, "DELETADOS_BI": None,
                "DUPLICADOS_REMOVIDOS": 0, "CORRECAO_APLICADA": False,
                "STATUS_TABELA": "TABELA_INEXISTENTE"
            }

        existe_bi = tabela_existe(conn_dest, schema_dest, tabela)

        # 1. Deduplicação: remove excedentes por R_E_C_N_O_ antes de comparar contagens
        if existe_bi:
            try:
                duplicados_antes = contar_duplicados(conn_dest, schema_dest, tabela)
            except Exception:
                logging.exception("Falha ao contar duplicados em %s, pulando deduplicação.", tabela)
                duplicados_antes = 0

            if duplicados_antes > 0:
                if pode_corrigir:
                    colunas, linhas = remover_duplicados(conn_dest, schema_dest, tabela)
                    duplicados_removidos = len(linhas)
                    registrar_log_duplicados(tabela, schema_dest, colunas, linhas)
                    logging.warning("  %s: %s registro(s) duplicado(s) removido(s) automaticamente.", tabela, duplicados_removidos)
                else:
                    logging.warning("  %s: %s registro(s) duplicado(s) detectado(s) (correção automática desabilitada).", tabela, duplicados_antes)

        # 2. Métricas pós-deduplicação
        prod = obter_metricas(conn_orig, schema_orig, tabela)
        bi = obter_metricas(conn_dest, schema_dest, tabela) if existe_bi else None

        divergente = (bi is None) or (
            prod["REGISTROS"] != bi["REGISTROS"]
            or prod["DELETADOS"] != bi["DELETADOS"]
        )

        # 3. Correção automática
        if divergente and pode_corrigir:
            # 3a. Primeiro tenta reconciliar exclusões físicas na origem (rápido, não mexe na
            # tabela inteira) — cobre a causa mais comum de divergência recorrente sem precisar
            # de recarga completa. O DIFF nunca apaga nada no destino, só insere/atualiza.
            if existe_bi:
                try:
                    removidos = reconciliar_exclusoes(
                        conn_orig, conn_dest, tabela, schema_orig, schema_dest, logger_replicacao,
                        limite_seguranca_pct=limite_seguranca_exclusoes_pct
                    )
                    if removidos:
                        correcao_aplicada = True
                        prod = obter_metricas(conn_orig, schema_orig, tabela)
                        bi = obter_metricas(conn_dest, schema_dest, tabela)
                        divergente = (
                            prod["REGISTROS"] != bi["REGISTROS"]
                            or prod["DELETADOS"] != bi["DELETADOS"]
                        )
                except Exception:
                    logging.exception("  %s: falha ao reconciliar exclusões, seguindo para recarga completa se necessário.", tabela)

            # 3b. Se ainda divergente (schema mudou, tabela nova, ou a reconciliação de
            # exclusões não foi suficiente), recarrega a tabela inteira a partir da origem
            # (via swap de tabela — completo_tabela não trunca mais a tabela em uso).
            if divergente:
                # Tabelas grandes recarregam com concorrência própria (menor), separada do pool
                # geral de validação: várias recargas pesadas ao mesmo tempo disputam a mesma
                # banda WAN até a origem e o mesmo destino local, o que deixa cada uma mais lenta
                # e aumenta a chance de a origem mudar no meio da cópia (gerando falso
                # FALHA_CORRECAO logo depois).
                tabela_grande = prod["REGISTROS"] >= limite_linhas_tabela_grande
                if tabela_grande:
                    logging.info("  %s: tabela grande (%s registros), aguardando vaga para recarregar...", tabela, prod["REGISTROS"])
                    semaforo_recarga_grande.acquire()
                try:
                    logging.info("  %s: divergência detectada, recarregando a partir da origem...", tabela)
                    completo_tabela(conn_orig, conn_dest, tabela, batch_size, logger_replicacao)
                    conn_dest.commit()
                    correcao_aplicada = True
                    # Reconsulta a origem: a recarga pode levar minutos numa tabela em uso ao vivo,
                    # então o "prod" capturado antes da recarga já pode estar desatualizado. Comparar
                    # contra ele geraria falso FALHA_CORRECAO mesmo quando a recarga funcionou.
                    prod = obter_metricas(conn_orig, schema_orig, tabela)
                    bi = obter_metricas(conn_dest, schema_dest, tabela)
                    existe_bi = True
                    divergente = (
                        prod["REGISTROS"] != bi["REGISTROS"]
                        or prod["DELETADOS"] != bi["DELETADOS"]
                    )
                except Exception:
                    logging.exception("  %s: falha ao corrigir automaticamente.", tabela)
                finally:
                    if tabela_grande:
                        semaforo_recarga_grande.release()

        if not existe_bi:
            status_tabela = "TABELA_INEXISTENTE"
        elif divergente:
            status_tabela = "FALHA_CORRECAO" if correcao_aplicada else "DIVERGENTE"
        elif correcao_aplicada or duplicados_removidos > 0:
            status_tabela = "CORRIGIDO"
        else:
            status_tabela = "OK"

        return {
            "TABELA": tabela,
            "REG_PRODUCAO": prod["REGISTROS"],
            "REG_BI": bi["REGISTROS"] if bi else None,
            "ULTIMA_ATUALIZACAO_PROD": prod["ULTIMA_ATUALIZACAO"],
            "ULTIMA_ATUALIZACAO_BI": bi["ULTIMA_ATUALIZACAO"] if bi else None,
            "DELETADOS_PROD": prod["DELETADOS"],
            "DELETADOS_BI": bi["DELETADOS"] if bi else None,
            "DUPLICADOS_REMOVIDOS": duplicados_removidos,
            "CORRECAO_APLICADA": correcao_aplicada,
            "STATUS_TABELA": status_tabela
        }
    except Exception as erro:
        logging.exception("Erro na validação da tabela %s", tabela)
        return {
            "TABELA": tabela,
            "REG_PRODUCAO": None, "REG_BI": None,
            "ULTIMA_ATUALIZACAO_PROD": None, "ULTIMA_ATUALIZACAO_BI": None,
            "DELETADOS_PROD": None, "DELETADOS_BI": None,
            "DUPLICADOS_REMOVIDOS": duplicados_removidos, "CORRECAO_APLICADA": correcao_aplicada,
            "STATUS_TABELA": f"ERRO: {str(erro)[:180]}"
        }
    finally:
        if conn_orig: conn_orig.close()
        if conn_dest: conn_dest.close()


def _levantar_tamanhos_origem(cfg, schema_orig: str, tabelas: list[str]) -> dict[str, int | None]:
    """COUNT(*) rápido (sem trazer dados) em cada tabela da origem, numa única conexão sequencial,
    usado só para decidir a ordem de execução (menores primeiro). Tabela cujo count falhar (ex: não
    existe) recebe None e é tratada como grande, indo para o fim da fila."""
    tamanhos: dict[str, int | None] = {}
    conn = None
    try:
        conn = get_conexao(cfg, "origem")
        cur = conn.cursor()
        for tabela in tabelas:
            try:
                cur.execute(f"SELECT COUNT(*) FROM [{schema_orig}].[{tabela}] WITH(NOLOCK)")
                row = cur.fetchone()
                tamanhos[tabela] = row[0] if row and row[0] is not None else 0
            except Exception:
                logging.warning("  %s: falha ao contar na origem para ordenação, será tratada como grande.", tabela)
                tamanhos[tabela] = None
    except Exception:
        logging.exception("Falha ao levantar tamanhos das tabelas na origem; mantendo ordem original da lista.")
        return {}
    finally:
        if conn: conn.close()
    return tamanhos


def executar_validacao(cfg, tabela_unica: str | None = None, auto_corrigir: bool = True):
    """Valida a integridade entre origem e BI. Quando auto_corrigir=True (padrão do job das 06:00),
    remove duplicados e recarrega automaticamente (via completo_tabela) qualquer tabela divergente
    ou ausente no BI antes de montar o relatório, funcionando como uma segunda passada de correção
    sobre o que a carga completa das 02:00 possa ter deixado divergente.

    As tabelas são processadas em paralelo (max_workers do config.ini), cada uma com suas
    próprias conexões — mesmo padrão de concorrência usado pelo job completo/diff em replicacao.py.

    Quando há correção a aplicar, um único lock de execução (o mesmo sp_getapplock do
    replicacao.py) é obtido ANTES do pool de threads e mantido até o fim — garante que nenhuma
    correção daqui (completo_tabela, remover_duplicados) rode ao mesmo tempo que um DIFF/COMPLETO
    em outra instância, sem precisar mais pausar a tarefa agendada do DIFF via schtasks."""
    resultados = []
    lock_conn = None
    batch_size = int(cfg['configuracao'].get('batch_size', '5000'))
    max_workers = max(1, int(cfg['configuracao'].get('max_workers', '4')))
    # Recargas de tabelas grandes usam um limite de concorrência próprio (menor), para não
    # saturar o link até a origem nem o destino quando várias divergem na mesma execução.
    limite_linhas_tabela_grande = int(cfg['configuracao'].get('limite_linhas_tabela_grande', '150000'))
    max_recargas_grandes = max(1, int(cfg['configuracao'].get('max_recargas_grandes_simultaneas', '2')))
    semaforo_recarga_grande = threading.Semaphore(max_recargas_grandes)
    # Acima desse % de "órfãos" no destino, a reconciliação de exclusões aborta sem apagar nada
    # (protege contra leitura parcial/instável da origem sendo interpretada como exclusão em massa).
    limite_seguranca_exclusoes_pct = float(cfg['configuracao'].get('limite_seguranca_exclusoes_pct', '0.05'))
    logger_replicacao = logging.getLogger()

    try:
        # Puxa o schema do arquivo config.ini ou usa 'dbo' como padrão
        schema_orig = cfg['origem'].get('schema', 'dbo')
        schema_dest = cfg['destino'].get('schema', 'dbo')

        tabelas = carregar_lista_tabelas(cfg, 'tabelas_completo', TABELAS_VALIDACAO_DEFAULT)
        if tabela_unica:
            lista_filtro = [t.strip().upper() for t in tabela_unica.split(',') if t.strip()]
            tabelas = [t for t in tabelas if t.upper() in lista_filtro]
            if not tabelas:
                logging.error("Nenhuma das tabelas '%s' encontrada na lista de validacao.", tabela_unica)
                sys.exit(1)

        total = len(tabelas)
        pode_corrigir = auto_corrigir and CORRECAO_AUTOMATICA_DISPONIVEL
        if auto_corrigir and not CORRECAO_AUTOMATICA_DISPONIVEL:
            logging.warning("[SISTEMA] Módulo 'replicacao.py' indisponível: correção automática desativada nesta execução.")

        if pode_corrigir:
            # Timeout bem mais longo que o do DIFF/COMPLETO (que só precisa detectar e pular um
            # ciclo de 5 min): aqui vale esperar um DIFF em andamento terminar, já que a correção
            # é importante e só roda uma vez por dia.
            timeout_ms = int(cfg['configuracao'].get('lock_timeout_validacao_ms', '120000'))
            lock_conn = adquirir_lock_execucao(cfg, timeout_ms=timeout_ms)
            if lock_conn is None:
                logging.warning(
                    "[SISTEMA] Não foi possível obter o lock de execução (outra replicação em "
                    "andamento por tempo demais). Correção automática desativada nesta execução; "
                    "seguindo só com a análise."
                )
                pode_corrigir = False

        # Ordena menor -> maior: assim tabelas pequenas não ficam presas na fila atrás de uma
        # tabela gigante que ocupou uma vaga do pool por dezenas de minutos, e as grandes só se
        # acumulam no fim da execução — onde o semáforo de recarga grande já limita a concorrência.
        logging.info("Levantando tamanho das tabelas na origem para ordenar a execução (menores primeiro)...")
        tamanhos_origem = _levantar_tamanhos_origem(cfg, schema_orig, tabelas)
        if tamanhos_origem:
            tabelas = sorted(
                tabelas,
                key=lambda t: tamanhos_origem[t] if tamanhos_origem.get(t) is not None else float('inf')
            )

        logging.info("Validando %s tabela(s) com %s worker(s) em paralelo...", total, max_workers)
        concluidas = 0
        progresso_lock = threading.Lock()
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _validar_uma_tabela, cfg, tabela, schema_orig, schema_dest,
                    batch_size, pode_corrigir, logger_replicacao,
                    limite_linhas_tabela_grande, semaforo_recarga_grande, limite_seguranca_exclusoes_pct
                ): tabela
                for tabela in tabelas
            }
            for future in concurrent.futures.as_completed(futures):
                tabela = futures[future]
                with progresso_lock:
                    concluidas += 1
                    indice = concluidas
                resultado = future.result()
                logging.info("[%s/%s] %s: %s", indice, total, tabela, resultado.get("STATUS_TABELA"))
                resultados.append(resultado)
        return resultados
    finally:
        liberar_lock_execucao(lock_conn)

def determinar_status(resultados: list[dict]) -> str:
    # Considera falha somente o que continuou divergente/ausente/com erro mesmo após a tentativa de correção.
    # CORRIGIDO não é falha: o problema foi detectado e resolvido automaticamente antes do e-mail sair.
    falhas = [
        r for r in resultados
        if str(r.get('STATUS_TABELA', '')).upper() in {'DIVERGENTE', 'FALHA_CORRECAO', 'TABELA_INEXISTENTE'}
        or 'ERRO' in str(r.get('STATUS_TABELA', '')).upper()
    ]
    return 'FALHA' if falhas else 'SUCESSO'


_STATUS_LABELS = {
    'OK': 'OK',
    'CORRIGIDO': 'CORRIGIDO AUTOMATICAMENTE',
    'DIVERGENTE': 'DIVERGENTE',
    'FALHA_CORRECAO': 'DIVERGENTE (falha ao corrigir)',
    'TABELA_INEXISTENTE': 'TABELA INEXISTENTE',
}


def montar_html(resultados: list[dict], data_execucao: str) -> str:
    status_geral = determinar_status(resultados)
    falhas = [r for r in resultados if str(r.get('STATUS_TABELA', '')).upper() in {'DIVERGENTE', 'FALHA_CORRECAO', 'TABELA_INEXISTENTE'} or 'ERRO' in str(r.get('STATUS_TABELA', '')).upper()]
    corrigidas = [r for r in resultados if str(r.get('STATUS_TABELA', '')).upper() == 'CORRIGIDO']
    ok = [r for r in resultados if str(r.get('STATUS_TABELA', '')).upper() == 'OK']
    total_duplicados_removidos = sum(int(r.get('DUPLICADOS_REMOVIDOS') or 0) for r in resultados)

    # Ordena para colocar o que for Crítico/Divergente no TOPO do e-mail, seguido do que foi corrigido, depois OK
    resultados_ordenados = falhas + corrigidas + ok

    linhas = []
    for item in resultados_ordenados:
        status = str(item.get('STATUS_TABELA', 'N/A')).upper()
        status_label = _STATUS_LABELS.get(status, status)

        if status in ('DIVERGENTE', 'FALHA_CORRECAO', 'TABELA_INEXISTENTE') or 'ERRO' in status:
            cor_linha = "#ffcccc"
        elif status == 'CORRIGIDO':
            cor_linha = "#fff3cd"
        else:
            cor_linha = "transparent"

        # Tratamento para exibir as datas formatadas de forma amigável
        ua_prod = item.get('ULTIMA_ATUALIZACAO_PROD')
        ua_bi = item.get('ULTIMA_ATUALIZACAO_BI')
        ua_prod_str = ua_prod.strftime('%Y-%m-%d %H:%M:%S') if isinstance(ua_prod, datetime) else str(ua_prod or 'N/A')
        ua_bi_str = ua_bi.strftime('%Y-%m-%d %H:%M:%S') if isinstance(ua_bi, datetime) else str(ua_bi or 'N/A')

        dup_removidos = int(item.get('DUPLICADOS_REMOVIDOS') or 0)

        linhas.append(
            f"<tr style='background-color: {cor_linha};'>"
            f"  <td>{item.get('TABELA','N/A')}</td>"
            f"  <td align='right'>{item.get('REG_PRODUCAO') if item.get('REG_PRODUCAO') is not None else 'N/A'}</td>"
            f"  <td align='right'>{item.get('REG_BI') if item.get('REG_BI') is not None else 'N/A'}</td>"
            f"  <td align='right'>{item.get('DELETADOS_PROD') if item.get('DELETADOS_PROD') is not None else 'N/A'}</td>"
            f"  <td align='right'>{item.get('DELETADOS_BI') if item.get('DELETADOS_BI') is not None else 'N/A'}</td>"
            f"  <td align='right'>{dup_removidos if dup_removidos else '-'}</td>"
            f"  <td>{ua_prod_str}</td>"
            f"  <td>{ua_bi_str}</td>"
            f"  <td><b>{status_label}</b></td>"
            f"</tr>"
        )

    cor_titulo = "#d9534f" if status_geral == 'FALHA' else "#5cb85c"

    corpo = f"""
    <html>
      <body style='font-family:Segoe UI, Arial, sans-serif; color:#333; margin:20px;'>
        <h2 style='color: {cor_titulo}; border-bottom: 2px solid {cor_titulo}; padding-bottom: 10px;'>Validação de Integridade de Dados - Replicação BI</h2>
        <p><b>Execução:</b> {data_execucao}</p>
        <p><b>Status geral:</b> <span style='padding:4px 8px; background:{cor_titulo}; color:white; font-weight:bold; border-radius:3px;'>{status_geral}</span></p>
        <p><b>Total de tabelas analisadas:</b> {len(resultados)}</p>
        <p>
          <span style='color: #d9534f; font-weight: bold;'>Divergentes/Erros: {len(falhas)}</span> &nbsp;|&nbsp;
          <span style='color: #b8860b; font-weight: bold;'>Corrigidas automaticamente: {len(corrigidas)}</span> &nbsp;|&nbsp;
          <span style='color: #5cb85c; font-weight: bold;'>OK: {len(ok)}</span> &nbsp;|&nbsp;
          <span style='color: #555;'>Registros duplicados removidos: {total_duplicados_removidos}</span>
        </p>

        <table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse; width:100%; min-width:960px; font-size:13px;'>
          <tr style='background:#f2f2f2; font-weight:bold;'>
            <th>Tabela</th>
            <th>Reg. Prod</th>
            <th>Reg. BI</th>
            <th>Del. Prod</th>
            <th>Del. BI</th>
            <th>Dup. Removidos</th>
            <th>U.A. Prod (S_T_A_M_P_)</th>
            <th>U.A. BI (S_T_A_M_P_)</th>
            <th>Status</th>
          </tr>
          {''.join(linhas)}
        </table>
        <p style='margin-top:20px; font-size:11px; color:#777;'>
          Este e-mail é automático. Antes de ser enviado, o job aplicou correção automática: registros duplicados (mesmo R_E_C_N_O_) foram
          removidos do BI e tabelas divergentes foram recarregadas integralmente a partir da origem. "Divergentes/Erros" indica apenas o que
          permaneceu divergente mesmo após essa tentativa de correção e precisa de atenção manual.
        </p>
      </body>
    </html>
    """
    return corpo

def enviar_email(cfg: configparser.ConfigParser, html: str, data_execucao: str, status_geral: str, houve_correcao: bool = False) -> None:
    smtp_cfg = cfg['email'] if 'email' in cfg else {}
    
    # Fallback para variáveis de ambiente
    servidor_smtp = os.environ.get('SMTP_SERVER') or smtp_cfg.get('servidor_smtp', '')
    porta_smtp_str = os.environ.get('SMTP_PORT') or smtp_cfg.get('porta_smtp', '587')
    porta_smtp = int(porta_smtp_str) if porta_smtp_str else 587
    usuario = os.environ.get('SMTP_USER') or smtp_cfg.get('usuario', '')
    senha = os.environ.get('SMTP_PASS') or smtp_cfg.get('senha', '')
    remetente = os.environ.get('SMTP_FROM') or smtp_cfg.get('remetente', usuario)
    destinatarios_str = os.environ.get('SMTP_TO') or smtp_cfg.get('destinatarios', '')

    msg = MIMEMultipart('alternative')
    if status_geral == 'FALHA':
        assunto = f"⚠️ ALERTA: divergência detectada no BI - {data_execucao}"
    elif houve_correcao:
        assunto = f"🔧 Validação BI - correções aplicadas automaticamente - {data_execucao}"
    else:
        assunto = f"✅ Validação de Integridade BI - OK - {data_execucao}"
    msg['Subject'] = assunto
    msg['From'] = remetente
    msg['To'] = destinatarios_str
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    with smtplib.SMTP(servidor_smtp, porta_smtp, timeout=30) as server:
        server.starttls()
        server.login(usuario, senha)
        server.sendmail(remetente, [x.strip() for x in destinatarios_str.split(',') if x.strip()], msg.as_string())

def configurar_log_validacao() -> Path:
    """Configura o logger raiz para gravar em logs/validacao/, além do console.

    Sem isso, a execução via Agendador de Tarefas do Windows não deixa nenhum
    rastro em disco: a saída do basicConfig ia só para o console, que o
    Task Scheduler descarta por rodar sem janela visível.
    """
    log_dir = ROOT_DIR / 'logs' / 'validacao'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"validacao_{datetime.now():%Y%m%d_%H%M%S}.log"

    fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    fh = logging.FileHandler(log_path, encoding='utf-8')
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    root_logger.addHandler(fh)
    root_logger.addHandler(sh)
    return log_path


def salvar_resumo_validacao(status_geral, resultados=None, email_enviado=None, email_erro=None):
    """Grava logs/validacao/ultimo_resultado.json com um resumo estruturado da última validação
    completa, para o dashboard consumir sem precisar fazer parsing de texto do log (frágil).
    Só é chamada para a validação de escopo completo (sem --tabela), que é o job das 06:00 —
    checagens manuais de uma tabela só não devem sobrescrever o resumo "oficial" do dia."""
    resultados = resultados or []
    falhas = [
        r for r in resultados
        if str(r.get('STATUS_TABELA', '')).upper() in {'DIVERGENTE', 'FALHA_CORRECAO', 'TABELA_INEXISTENTE'}
        or 'ERRO' in str(r.get('STATUS_TABELA', '')).upper()
    ]
    corrigidas = [r for r in resultados if str(r.get('STATUS_TABELA', '')).upper() == 'CORRIGIDO']
    resumo = {
        "data_execucao": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "status_geral": status_geral,
        "total_tabelas": len(resultados),
        "tabelas_divergentes": len(falhas),
        "tabelas_corrigidas": len(corrigidas),
        "duplicados_removidos": sum(int(r.get('DUPLICADOS_REMOVIDOS') or 0) for r in resultados),
        "email_enviado": email_enviado,
        "email_erro": email_erro,
    }
    try:
        RESUMO_VALIDACAO_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RESUMO_VALIDACAO_PATH, 'w', encoding='utf-8') as f:
            json.dump(resumo, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.exception("Falha ao gravar o resumo de validação em %s (não afeta o restante da execução).", RESUMO_VALIDACAO_PATH)


def main() -> int:
    log_path = configurar_log_validacao()
    logging.info("Log desta execução: %s", log_path)

    parser = argparse.ArgumentParser(description='Validador de Integridade de Dados - Replicação BI')
    parser.add_argument('--config', default=None, help='Caminho do arquivo config.ini')
    parser.add_argument('--tabela', default=None, help='Validar apenas uma tabela específica')
    parser.add_argument(
        '--somente-analise', action='store_true',
        help='Executa apenas a validação, sem remover duplicados nem recarregar tabelas divergentes (padrão do job das 06:00 é corrigir automaticamente).'
    )
    args = parser.parse_args()
    validacao_completa = not args.tabela  # só o escopo completo (job das 06:00) atualiza o resumo do dashboard

    try:
        cfg = carregar_config(args.config)
    except Exception:
        logging.exception("Falha ao carregar configuração — nenhum e-mail foi enviado.")
        if validacao_completa:
            salvar_resumo_validacao("ERRO_EXECUCAO", email_enviado=False)
        return 1

    execucao_id = db_historico.registrar_inicio_execucao(cfg, 'validacao', tabela_alvo=args.tabela)

    try:
        resultados = executar_validacao(cfg, args.tabela, auto_corrigir=not args.somente_analise)
        data_execucao = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        status_geral = determinar_status(resultados)
        houve_correcao = any(str(r.get('STATUS_TABELA', '')).upper() == 'CORRIGIDO' for r in resultados)
        html = montar_html(resultados, data_execucao)
    except Exception as e:
        logging.exception("Falha ao executar a validação de integridade — nenhum e-mail foi enviado.")
        if validacao_completa:
            salvar_resumo_validacao("ERRO_EXECUCAO", email_enviado=False)
        db_historico.registrar_fim_execucao(cfg, execucao_id, status='FALHA', erro=str(e))
        return 1

    # Detalhe por tabela pro histórico: registros/tempo não são rastreados pelo validador hoje, só o status.
    detalhes_tabelas = [(r.get('TABELA', '?'), None, str(r.get('STATUS_TABELA', ''))) for r in resultados]

    try:
        enviar_email(cfg, html, data_execucao, status_geral, houve_correcao=houve_correcao)
    except Exception:
        logging.exception("Validação concluída (status: %s), mas houve falha ao ENVIAR O E-MAIL de notificação.", status_geral)
        if validacao_completa:
            salvar_resumo_validacao(status_geral, resultados, email_enviado=False, email_erro="Falha ao enviar e-mail (ver log desta execução)")
        db_historico.registrar_fim_execucao(
            cfg, execucao_id, status=status_geral, tabelas_atualizadas=len(resultados),
            erro="Falha ao enviar e-mail de notificação (ver log desta execução)", detalhes_tabelas=detalhes_tabelas
        )
        return 1

    if validacao_completa:
        salvar_resumo_validacao(status_geral, resultados, email_enviado=True)

    db_historico.registrar_fim_execucao(
        cfg, execucao_id, status=status_geral, tabelas_atualizadas=len(resultados), detalhes_tabelas=detalhes_tabelas
    )

    logging.info('Validação concluída com %s tabelas analisadas. Status: %s. E-mail enviado com sucesso.', len(resultados), status_geral)
    return 0

if __name__ == '__main__':
    sys.exit(main())
