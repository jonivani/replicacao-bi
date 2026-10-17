"""
Lógica de negócio compartilhada entre os backends do painel (Flask em `app.py`,
FastAPI em `backend/main.py` — Fase 3a do roadmap v2, ver docs/ROADMAP_V2.md).

Nada aqui depende de Flask nem de FastAPI: é puro Python + subprocess/pyodbc/psutil,
consumido pelos dois. Extraído do `app.py` original pra não duplicar ~500 linhas
entre os dois backends durante a migração — quando o Flask for desligado, este
módulo continua sendo a base do FastAPI sem precisar reescrever nada de novo.
"""

import sys
import os
import re
import time
import subprocess
import threading
import logging
import configparser
import smtplib
import json
from pathlib import Path
from datetime import datetime

import pyodbc

try:
    import psutil
    PSUTIL_DISPONIVEL = True
except Exception:
    PSUTIL_DISPONIVEL = False

# Configuração de diretórios
ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / 'config' / 'config.ini'

# Tenta importar o módulo de relatório para reuso
try:
    sys.path.append(str(ROOT_DIR / 'scripts'))
    from email_notificacao import RelatorioReplicacao
    EMAIL_PARSER_DISPONIVEL = True
except Exception:
    EMAIL_PARSER_DISPONIVEL = False

try:
    from replicacao import TABELAS_DIFF_DEFAULT, TABELAS_COMPLETO_DEFAULT
except Exception:
    TABELAS_DIFF_DEFAULT = []
    TABELAS_COMPLETO_DEFAULT = []

import db_historico
import speedtest_servico

# Estado global da tarefa ativa
active_task = {
    "running": False,
    "mode": None,
    "smart": False,  # só relevante quando mode == "completo" — ver run_replication_subprocess
    "tabela": None,
    "start_time": None,
    "output": [],
    "exit_code": None,
    "error": None
}

# Estado global da auditoria de dados em tempo real
active_audit = {
    "running": False,
    "total": 0,
    "processadas": 0,
    "resultados": [],
    "error": None,
    "finished_at": None
}

# Estado global do teste de velocidade de internet sob demanda (card "Velocidade da Internet"
# no Painel Geral) — mesmo padrão de active_task/active_audit.
active_speedtest = {
    "running": False,
    "started_at": None,
    "fase": None,          # 'ping' | 'download' | 'upload' | None
    "parcial_mbps": None,  # estimativa ao vivo durante download/upload, pro card animar como o fast.com
    "resultado": None,     # dict final (ver speedtest_servico.executar_speedtest) quando running=False
    "error": None,
}


def run_speedtest_thread(origem: str = 'manual') -> None:
    """Roda o teste de velocidade em background, atualizando active_speedtest conforme mede —
    pro card do Painel Geral mostrar o número subindo ao vivo via polling, em vez de só
    aparecer no final."""
    active_speedtest["running"] = True
    active_speedtest["started_at"] = time.time()
    active_speedtest["fase"] = 'ping'
    active_speedtest["parcial_mbps"] = None
    active_speedtest["error"] = None
    active_speedtest["resultado"] = None

    def progresso(fase, bytes_ate_agora, segundos):
        active_speedtest["fase"] = fase
        if segundos > 0:
            active_speedtest["parcial_mbps"] = round((bytes_ate_agora * 8 / 1_000_000) / segundos, 2)

    try:
        resultado = speedtest_servico.executar_speedtest(origem=origem, progress_cb=progresso)
        active_speedtest["resultado"] = resultado
        speedtest_servico.salvar_resultado(resultado)
        if resultado.get("error"):
            active_speedtest["error"] = resultado["error"]
    except Exception as e:
        active_speedtest["error"] = str(e)
    finally:
        active_speedtest["running"] = False
        active_speedtest["fase"] = None

# Cache de taxa de sucesso por modo, para o /api/status não reprocessar centenas de arquivos
# de log a cada poll (a aba de agendamentos/dashboard consulta esse endpoint a cada 5s quando
# há tarefa rodando).
_taxa_sucesso_cache = {}
_TAXA_SUCESSO_CACHE_TTL = 300  # 5 minutos

SCHEDULED_TASK_NAMES = [
    "Protheus BI - Replicacao DIFF",
    "Protheus BI - Replicacao COMPLETO",
    "Protheus BI - Validacao Integridade"
]

# Cache curto do status das 3 tarefas agendadas: /api/status e /api/schedules consultam as mesmas
# 3 tarefas, e cada consulta ao Windows Task Scheduler via PowerShell custa um spawn de processo
# (em alguns servidores, com antivírus escaneando cada powershell.exe iniciado, isso pode levar
# vários segundos). O TTL absorve rajadas de polls quase simultâneos sem deixar o painel exibindo
# um estado perceptivelmente desatualizado. O lock garante que, mesmo com várias requisições HTTP
# concorrentes batendo com o cache expirado ao mesmo tempo, só UM processo PowerShell é disparado
# por vez — as demais esperam o lock e reaproveitam o resultado que acabou de ser calculado, em vez
# de cada uma spawnar o seu próprio powershell.exe (o que gerava disputa de CPU e piorava a lentidão).
_schedule_status_cache = {"data": None, "ts": 0.0}
_schedule_status_lock = threading.Lock()
_SCHEDULE_STATUS_CACHE_TTL = 15  # segundos

MASK_STRING = "••••••••"

MAX_LOGS_LISTADOS_POR_MODO = 200


def resolver_caminho(caminho):
    if caminho is None:
        return DEFAULT_CONFIG_PATH
    path = Path(caminho)
    if path.is_absolute():
        return path
    candidatos = [Path.cwd() / path, ROOT_DIR / path]
    for candidato in candidatos:
        if candidato.exists():
            return candidato.resolve()
    return (ROOT_DIR / path).resolve()


def carregar_cfg():
    """Carrega o config.ini atual. Lido a cada chamada (não cacheado) porque a aba
    Configurações permite editá-lo em runtime."""
    cfg = configparser.ConfigParser()
    if DEFAULT_CONFIG_PATH.exists():
        cfg.read(DEFAULT_CONFIG_PATH, encoding='utf-8-sig')
    return cfg


def _iterar_logs(log_dir):
    """Itera os arquivos .log de um diretório usando os.scandir (não Path.glob + Path.stat).

    No Windows, DirEntry.stat() reaproveita os metadados que o próprio FindNextFile já retornou,
    evitando uma syscall extra por arquivo. Com Path.glob()+Path.stat() cada arquivo custa 2-3
    stats separados — em diretórios com milhares de logs (o DIFF automático roda a cada poucos
    minutos) isso é a diferença entre milissegundos e dezenas de segundos.
    """
    with os.scandir(log_dir) as it:
        for entry in it:
            if entry.is_file() and entry.name.endswith('.log'):
                yield entry


def get_latest_log_file(mode):
    """Retorna o caminho do arquivo de log mais recente para o modo especificado."""
    log_dir = ROOT_DIR / 'logs' / mode
    if not log_dir.exists():
        return None
    mais_recente, mtime_mais_recente = None, -1.0
    for entry in _iterar_logs(log_dir):
        mtime = entry.stat().st_mtime
        if mtime > mtime_mais_recente:
            mais_recente, mtime_mais_recente = entry, mtime
    return Path(mais_recente.path) if mais_recente else None


_PADRAO_TOTAL_REPLICACAO = re.compile(r'Tabelas:\s+(\d+)')
_PADRAO_TOTAL_VALIDACAO = re.compile(r'Validando\s+(\d+)\s+tabela')
_PADRAO_PROGRESSO_VALIDACAO = re.compile(r'\[(\d+)/(\d+)\]')
_PADRAO_TABELA_TERMINAL = re.compile(
    r'\s+(\w+)(?:\s+\[\w+\])?: (?:finalizada com sucesso|sem novos registros|ERRO)'
)


def calcular_progresso_log(modo, conteudo):
    """Deriva um progresso aproximado (tabelas concluídas / total) a partir do texto do log —
    os scripts não expõem isso de forma estruturada, então isso é uma heurística sobre as
    mesmas mensagens que o parser de e-mail (email_notificacao.py) já usa.

    Retorna None quando não dá pra estimar ainda (ex: log recém-criado, sem a linha de cabeçalho)."""
    if modo == "validacao":
        # O validador já loga "[concluidas/total]" por tabela concluída — pega a última ocorrência.
        matches = _PADRAO_PROGRESSO_VALIDACAO.findall(conteudo)
        if matches:
            atual, total = matches[-1]
            return {"atual": int(atual), "total": int(total)}
        match_total = _PADRAO_TOTAL_VALIDACAO.search(conteudo)
        if match_total:
            return {"atual": 0, "total": int(match_total.group(1))}
        return None

    # DIFF/COMPLETO: o total vem da linha de cabeçalho ("Modo: ... | Tabelas: N | Batch: ...");
    # o "atual" é a quantidade de tabelas distintas que já chegaram a uma linha terminal.
    match_total = _PADRAO_TOTAL_REPLICACAO.search(conteudo)
    if not match_total:
        return None
    total = int(match_total.group(1))
    tabelas_vistas = {m.group(1).upper() for m in _PADRAO_TABELA_TERMINAL.finditer(conteudo)}
    return {"atual": min(len(tabelas_vistas), total), "total": total}


def calcular_taxa_sucesso(modo, dias=7):
    """Calcula a % de execuções com sucesso de um modo (diff/completo) nos últimos `dias`.

    Fase 1 do roadmap v2: lê de REPLICACAO_EXECUCOES (db_historico.py) em vez de escanear e
    fazer parsing de arquivos .log. Resultado ainda é cacheado por alguns minutos — mesmo uma
    consulta SQL indexada não precisa rodar a cada poll do /api/status.
    """
    agora = time.time()
    cached = _taxa_sucesso_cache.get(modo)
    if cached and (agora - cached[0]) < _TAXA_SUCESSO_CACHE_TTL:
        return cached[1]

    resultado = db_historico.consultar_taxa_sucesso(carregar_cfg(), modo, dias=dias)
    _taxa_sucesso_cache[modo] = (agora, resultado)
    return resultado


_BYTES_POR_GB = 1024 ** 3


def coletar_saude_servidor():
    """Retorna uso de CPU, memória RAM e disco do servidor onde o app roda.

    cpu_percent(interval=None) é não-bloqueante: compara com a última chamada em vez de esperar
    uma janela de amostragem, então não adiciona latência ao /api/status mesmo sendo chamado com
    frequência (o valor só fica preciso a partir da segunda chamada, o que é aceitável aqui).
    """
    if not PSUTIL_DISPONIVEL:
        return None
    try:
        mem = psutil.virtual_memory()
        disco = psutil.disk_usage(str(ROOT_DIR.anchor or ROOT_DIR))
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memoria": {
                "percent": mem.percent,
                "usado_gb": round(mem.used / _BYTES_POR_GB, 1),
                "total_gb": round(mem.total / _BYTES_POR_GB, 1)
            },
            "disco": {
                "percent": disco.percent,
                "usado_gb": round(disco.used / _BYTES_POR_GB, 1),
                "total_gb": round(disco.total / _BYTES_POR_GB, 1),
                "livre_gb": round(disco.free / _BYTES_POR_GB, 1)
            }
        }
    except Exception as e:
        logging.debug(f"Falha ao coletar saúde do servidor: {e}")
        return None


def testar_conexao_db(server, port, database, user, password, driver):
    """Tenta conectar ao SQL Server para testar parâmetros."""
    if ',' in server or '\\' in server:
        server_str = server
    else:
        server_str = f"{server},{port}" if port else server

    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server_str};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        f"TrustServerCertificate=yes;"
        f"Encrypt=yes;"
        f"Connection Timeout=5;"  # Timeout curto para teste de UI
    )
    conn = None
    try:
        conn = pyodbc.connect(conn_str)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        return True, "Conexão estabelecida com sucesso."
    except Exception as e:
        return False, str(e)
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


def testar_conexao_smtp(server, port, user, password):
    """Tenta conectar ao servidor SMTP para testar credenciais."""
    try:
        with smtplib.SMTP(server, int(port), timeout=5) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(user, password)
            return True, "Conexão SMTP e login autenticados com sucesso."
    except Exception as e:
        return False, str(e)


def run_replication_subprocess(modo, tabela=None, smart=False):
    """Executa a replicação em um subprocesso e lê a saída em tempo real.

    `smart` só é relevante quando `modo == 'completo'` — substitui o antigo modo sintético
    'completo_smart' (Fase 2 do roadmap v2: ver docs/API_CONTRACT.md, inconsistência #4).

    Não precisa mais pausar a tarefa agendada do DIFF antes de uma carga completa manual: o
    replicacao.py agora adquire, logo no início de executar(), um lock de execução exclusivo
    (sp_getapplock no destino). Se uma segunda instância — agendada ou manual, DIFF ou COMPLETO —
    tentar rodar ao mesmo tempo, ela mesma detecta o lock ocupado e se ignora sozinha, sem exigir
    coordenação daqui e sem risco de duplicar registros no destino."""
    global active_task
    config_path = str(DEFAULT_CONFIG_PATH)

    # Monta comando do Python
    script_path = str(ROOT_DIR / "scripts" / "replicacao.py")

    # Configura ambiente unbuffered para Python
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    cmd = [sys.executable, "-u", script_path, "--modo", modo, "--config", config_path]
    if smart:
        cmd.append("--smart")
    if tabela:
        cmd.extend(["--tabela", tabela])

    try:
        active_task["running"] = True
        active_task["mode"] = modo
        active_task["smart"] = smart
        active_task["tabela"] = tabela
        active_task["start_time"] = time.time()
        active_task["output"] = ["$ " + " ".join(cmd) + "\n"]
        active_task["exit_code"] = None
        active_task["error"] = None

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(ROOT_DIR),
            env=env
        )

        while True:
            line = process.stdout.readline()
            if not line:
                break
            active_task["output"].append(line)
            # Limita a 500 linhas na memória para evitar sobrecarga de rede
            if len(active_task["output"]) > 500:
                active_task["output"].pop(0)

        process.wait()
        active_task["exit_code"] = process.returncode
        if process.returncode == 0:
            active_task["output"].append("\n[PROCESSO CONCLUÍDO COM SUCESSO]\n")
        else:
            active_task["output"].append(f"\n[PROCESSO FALHOU - CÓDIGO DE SAÍDA: {process.returncode}]\n")
            active_task["error"] = f"Processo terminou com código de saída {process.returncode}"
    except Exception as e:
        active_task["error"] = str(e)
        active_task["output"].append(f"\n[EXCEÇÃO NO SERVER: {str(e)}]\n")
    finally:
        active_task["running"] = False


def parse_duration_seconds(duration_str):
    """Auxiliar para converter strings de tempo de log (h:mm:ss.fff ou mm:ss) em segundos decimais."""
    if not duration_str:
        return 0.0
    try:
        parts = duration_str.split(':')
        if len(parts) == 3:
            h = float(parts[0])
            m = float(parts[1])
            s = float(parts[2])
            return h * 3600 + m * 60 + s
        elif len(parts) == 2:
            m = float(parts[0])
            s = float(parts[1])
            return m * 60 + s
        else:
            return float(duration_str)
    except Exception:
        return 0.0


def run_realtime_audit(tabelas_lista):
    """Executa a validação de integridade tabela por tabela e atualiza o estado global."""
    global active_audit
    active_audit["running"] = True
    active_audit["total"] = len(tabelas_lista)
    active_audit["processadas"] = 0
    active_audit["resultados"] = []
    active_audit["error"] = None

    conn_orig = None
    conn_dest = None

    try:
        sys.path.append(str(ROOT_DIR / 'scripts'))
        import validador_integridade as val

        cfg = carregar_cfg()

        schema_orig = cfg['origem'].get('schema', 'dbo')
        schema_dest = cfg['destino'].get('schema', 'dbo')

        conn_orig = val.get_conexao(cfg, "origem")
        conn_dest = val.get_conexao(cfg, "destino")

        for tab in tabelas_lista:
            if not active_audit["running"]:
                break

            try:
                existe_prod = val.tabela_existe(conn_orig, schema_orig, tab)
                existe_bi = val.tabela_existe(conn_dest, schema_dest, tab)

                if not existe_prod or not existe_bi:
                    status_tab = "INEXISTENTE"
                    if not existe_prod and not existe_bi:
                        status_tab = "INEXISTENTE_AMBOS"
                    elif not existe_prod:
                        status_tab = "INEXISTENTE_ORIGEM"
                    else:
                        status_tab = "INEXISTENTE_DESTINO"

                    res = {
                        "tabela": tab,
                        "reg_producao": None,
                        "reg_bi": None,
                        "deletados_prod": None,
                        "deletados_bi": None,
                        "stamp_prod": None,
                        "stamp_bi": None,
                        "duplicados_bi": None,
                        "status_tabela": status_tab
                    }
                else:
                    prod = val.obter_metricas(conn_orig, schema_orig, tab)
                    bi = val.obter_metricas(conn_dest, schema_dest, tab)

                    divergencia = (
                        prod["REGISTROS"] != bi["REGISTROS"]
                        or prod["DELETADOS"] != bi["DELETADOS"]
                    )

                    try:
                        duplicados_bi = val.contar_duplicados(conn_dest, schema_dest, tab)
                    except Exception:
                        duplicados_bi = 0

                    stamp_prod_str = prod["ULTIMA_ATUALIZACAO"].strftime('%Y-%m-%d %H:%M:%S') if isinstance(prod["ULTIMA_ATUALIZACAO"], datetime) else str(prod["ULTIMA_ATUALIZACAO"] or 'N/A')
                    stamp_bi_str = bi["ULTIMA_ATUALIZACAO"].strftime('%Y-%m-%d %H:%M:%S') if isinstance(bi["ULTIMA_ATUALIZACAO"], datetime) else str(bi["ULTIMA_ATUALIZACAO"] or 'N/A')

                    status_tab = "DIVERGENTE" if divergencia else "OK"
                    if duplicados_bi > 0 and status_tab == "OK":
                        status_tab = "DUPLICADO"

                    res = {
                        "tabela": tab,
                        "reg_producao": prod["REGISTROS"],
                        "reg_bi": bi["REGISTROS"],
                        "deletados_prod": prod["DELETADOS"],
                        "deletados_bi": bi["DELETADOS"],
                        "stamp_prod": stamp_prod_str,
                        "stamp_bi": stamp_bi_str,
                        "duplicados_bi": duplicados_bi,
                        "status_tabela": status_tab
                    }
            except Exception as e:
                res = {
                    "tabela": tab,
                    "reg_producao": None,
                    "reg_bi": None,
                    "deletados_prod": None,
                    "deletados_bi": None,
                    "stamp_prod": None,
                    "stamp_bi": None,
                    "duplicados_bi": None,
                    "status_tabela": f"ERRO: {str(e)}"
                }

            active_audit["resultados"].append(res)
            active_audit["processadas"] += 1

    except Exception as e:
        active_audit["error"] = str(e)
    finally:
        active_audit["running"] = False
        active_audit["finished_at"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if conn_orig:
            try: conn_orig.close()
            except: pass
        if conn_dest:
            try: conn_dest.close()
            except: pass


def _formatar_task_info(task_name, data):
    """Converte o objeto retornado pelo PowerShell para o formato usado pela API. `data` é None
    quando a tarefa não existe ou a consulta falhou."""
    if not data:
        return {
            "installed": False,
            "name": task_name,
            "next_run": "N/A",
            "status": "Não Instalada",
            "last_run": "N/A",
            "last_result": "N/A"
        }

    # Settings.Enabled tem prioridade sobre o State de execução para refletir corretamente
    # uma tarefa pausada (Disable-ScheduledTask), já que o State por si só reflete Ready/Running/Queued.
    estado = str(data.get('State', 'Ready'))
    if data.get('Enabled') is False:
        status = "Desativado"
    elif estado.lower() == "running":
        status = "Executando"
    else:
        status = "Pronto"

    last_result = data.get('LastTaskResult')

    return {
        "installed": True,
        "name": task_name,
        "next_run": data.get('NextRunTime') or "N/A",
        "status": status,
        "last_run": data.get('LastRunTime') or "N/A",
        "last_result": str(last_result) if last_result is not None else "N/A"
    }


def get_all_schedule_statuses(forcar_atualizacao=False):
    """Consulta o status das 3 tarefas agendadas via PowerShell (Get-ScheduledTask/Get-ScheduledTaskInfo).

    Evita fazer parsing de texto da saída do `schtasks /query /fo list`, cujos rótulos de campo são
    localizados (ex: PT-BR) e não batem com os literais em inglês antes usados aqui — isso fazia o
    status ficar preso no valor padrão 'Pronto' mesmo com a tarefa desativada ou em execução.
    Get-ScheduledTask/-Info retornam valores tipados e independentes de idioma (State é sempre em inglês).

    Consulta as 3 tarefas em UM único processo PowerShell (em vez de um spawn por tarefa) e usa um
    cache curto (_SCHEDULE_STATUS_CACHE_TTL) — /api/status e /api/schedules consultam as mesmas 3
    tarefas, e /api/status sozinho fazia 3 spawns de powershell.exe a cada chamada.
    """
    agora = time.time()
    cache = _schedule_status_cache
    if not forcar_atualizacao and cache["data"] is not None and (agora - cache["ts"]) < _SCHEDULE_STATUS_CACHE_TTL:
        return cache["data"]

    with _schedule_status_lock:
        # Reconfere o cache já com o lock em mãos: se outra requisição chegou primeiro e acabou de
        # atualizar enquanto esperávamos, reaproveita o resultado dela em vez de rodar PowerShell de novo.
        agora = time.time()
        if not forcar_atualizacao and cache["data"] is not None and (agora - cache["ts"]) < _SCHEDULE_STATUS_CACHE_TTL:
            return cache["data"]

        nomes_ps = ", ".join("'" + nome.replace("'", "''") + "'" for nome in SCHEDULED_TASK_NAMES)
        ps_script = (
            "$ErrorActionPreference = 'Stop'\n"
            f"$nomes = @({nomes_ps})\n"
            "$resultado = foreach ($nome in $nomes) {\n"
            "    try {\n"
            "        $t = Get-ScheduledTask -TaskName $nome\n"
            "        $i = $t | Get-ScheduledTaskInfo\n"
            "        [PSCustomObject]@{\n"
            "            TaskName = $nome\n"
            "            Found = $true\n"
            "            State = $t.State.ToString()\n"
            "            Enabled = $t.Settings.Enabled\n"
            "            NextRunTime = if ($i.NextRunTime) { $i.NextRunTime.ToString('dd/MM/yyyy HH:mm:ss') } else { '' }\n"
            "            LastRunTime = if ($i.LastRunTime) { $i.LastRunTime.ToString('dd/MM/yyyy HH:mm:ss') } else { '' }\n"
            "            LastTaskResult = $i.LastTaskResult\n"
            "        }\n"
            "    } catch {\n"
            "        [PSCustomObject]@{ TaskName = $nome; Found = $false }\n"
            "    }\n"
            "}\n"
            "$resultado | ConvertTo-Json -Compress\n"
        )

        por_nome = {}
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20
            )
            if res.returncode != 0 or not res.stdout.strip():
                raise RuntimeError((res.stderr or b'').decode('utf-8', errors='replace').strip())

            data = json.loads(res.stdout.decode('utf-8', errors='replace'))
            if isinstance(data, dict):
                data = [data]
            for item in data:
                if item.get('Found'):
                    por_nome[item.get('TaskName')] = item
        except Exception as e:
            logging.debug(f"Falha ao consultar tarefas agendadas via PowerShell: {e}")

        resultado = [_formatar_task_info(nome, por_nome.get(nome)) for nome in SCHEDULED_TASK_NAMES]
        cache["data"] = resultado
        cache["ts"] = agora
        return resultado
