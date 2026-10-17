"""
Backend FastAPI — Fase 3a do roadmap v2 (ver docs/ROADMAP_V2.md), substituindo o app.py Flask.

Consome scripts/servicos.py (mesma lógica de negócio usada pelo Flask durante a migração),
só a camada HTTP muda. Contrato de resposta é o mesmo já padronizado na Fase 2
(docs/API_CONTRACT.md): erro sempre `{"error": {"message": "..."}}`, sucesso de POST de ação
sempre `{"success": true, "data": {...}}`, GETs de leitura devolvem os dados direto na raiz.
"""

import sys
import os
import subprocess
import threading
import logging
import configparser
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent
sys.path.append(str(ROOT_DIR / 'scripts'))

import servicos
import speedtest_servico
from servicos import (
    DEFAULT_CONFIG_PATH, TABELAS_DIFF_DEFAULT, TABELAS_COMPLETO_DEFAULT,
    active_task, active_audit, active_speedtest, SCHEDULED_TASK_NAMES, MASK_STRING, MAX_LOGS_LISTADOS_POR_MODO,
    carregar_cfg, get_latest_log_file, calcular_progresso_log, calcular_taxa_sucesso,
    coletar_saude_servidor, testar_conexao_db, testar_conexao_smtp,
    run_replication_subprocess, parse_duration_seconds, run_realtime_audit, run_speedtest_thread,
    get_all_schedule_statuses, _iterar_logs,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI(title="Protheus BI — API v2 (FastAPI)")

# CORS: em desenvolvimento o Angular roda em porta própria (ng serve, padrão 4200) enquanto
# este backend roda na 5050 (ou a porta do config.ini) — sem isso o navegador bloqueia as
# chamadas por serem de origem diferente. Em produção, quando o build do Angular for servido
# por este mesmo processo (Fase 3a, tarefa 6), isso deixa de ser necessário mas não atrapalha.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===========================================================================
# ENVELOPE PADRÃO DE RESPOSTA (Fase 2 do roadmap v2 — docs/API_CONTRACT.md)
# ===========================================================================

class ErroAPI(Exception):
    """Levantada pelos endpoints pra sinalizar um erro de request — vira
    `{"error": {"message": "..."}}` com o status HTTP correspondente."""
    def __init__(self, mensagem: str, status_code: int = 400):
        self.mensagem = mensagem
        self.status_code = status_code


@app.exception_handler(ErroAPI)
async def erro_api_handler(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status_code, content={"error": {"message": exc.mensagem}})


@app.exception_handler(RequestValidationError)
async def erro_validacao_handler(request: Request, exc: RequestValidationError):
    """Sem isso, um campo obrigatório faltando no body (ex: POST /api/run sem `modo`) responde
    422 no formato padrão do FastAPI (`{"detail": [...]}`), quebrando o envelope de erro único
    que a Fase 2 padronizou. Junta as mensagens de todos os campos inválidos numa só string."""
    detalhes = "; ".join(
        f"{'.'.join(str(p) for p in erro['loc'])}: {erro['msg']}" for erro in exc.errors()
    )
    return JSONResponse(status_code=422, content={"error": {"message": f"Payload inválido — {detalhes}"}})


def resposta_sucesso(data: Optional[dict] = None, sucesso: bool = True) -> dict:
    """Envelope padrão de sucesso pra POSTs de ação — mesma forma usada no Flask
    (`{"success": bool, "data": {...}}`). GETs de leitura devolvem os dados direto,
    sem passar por aqui."""
    return {"success": sucesso, "data": data or {}}


# ===========================================================================
# MODELOS DE REQUEST (Pydantic)
# ===========================================================================

class ConfigTestBody(BaseModel):
    type: str
    data: dict = {}


class RunBody(BaseModel):
    modo: str
    tabela: Optional[str] = None
    smart: bool = False


class AuditStartBody(BaseModel):
    tabelas: Optional[str] = None


class RemoveDuplicatesBody(BaseModel):
    tabela: str


class ScheduleActionBody(BaseModel):
    task: str
    action: str


# ===========================================================================
# ROTAS
# ===========================================================================

@app.get('/api/status')
def get_status():
    """Retorna o status geral da replicação e últimas estatísticas."""
    import time
    stats: dict[str, Any] = {
        "sistema": "ONLINE",
        "last_diff": None,
        "last_completo": None,
        "active_task": {
            "running": active_task["running"],
            "mode": active_task["mode"],
            "smart": active_task["smart"],
            "tabela": active_task["tabela"],
            "duration": round(time.time() - active_task["start_time"], 2) if active_task["running"] and active_task["start_time"] else 0
        },
        "schedules": get_all_schedule_statuses(),
        "server_health": coletar_saude_servidor(),
        "last_validacao": None,
        "ultima_auditoria": None,
        "last_speedtest": speedtest_servico.carregar_ultimo_resultado(),
        "taxa_sucesso": {
            "diff": calcular_taxa_sucesso("diff"),
            "completo": calcular_taxa_sucesso("completo")
        }
    }

    resumo_validacao_path = ROOT_DIR / 'logs' / 'validacao' / 'ultimo_resultado.json'
    if resumo_validacao_path.exists():
        try:
            stats["last_validacao"] = json.loads(resumo_validacao_path.read_text(encoding='utf-8-sig'))
        except Exception as e:
            stats["last_validacao"] = {"error": str(e)}

    if active_audit["resultados"]:
        resultados_audit = active_audit["resultados"]
        tabelas_com_duplicados = [r for r in resultados_audit if isinstance(r.get("duplicados_bi"), int) and r["duplicados_bi"] > 0]
        tabelas_divergentes = [r for r in resultados_audit if r.get("status_tabela") == "DIVERGENTE"]
        deletados_bi = sum(r["deletados_bi"] for r in resultados_audit if isinstance(r.get("deletados_bi"), int))
        stats["ultima_auditoria"] = {
            "quando": active_audit.get("finished_at"),
            "tabelas_auditadas": len(resultados_audit),
            "tabelas_duplicadas": len(tabelas_com_duplicados),
            "duplicados_registros": sum(r["duplicados_bi"] for r in tabelas_com_duplicados),
            "tabelas_divergentes": len(tabelas_divergentes),
            "registros_deletados_bi": deletados_bi
        }

    latest_diff = get_latest_log_file("diff")
    if latest_diff and servicos.EMAIL_PARSER_DISPONIVEL:
        try:
            rel = servicos.RelatorioReplicacao(latest_diff)
            dur_seg = parse_duration_seconds(rel.duracao)
            vazao = round(rel.total_inseridos / dur_seg, 1) if dur_seg > 0 else 0.0
            stats["last_diff"] = {
                "filename": latest_diff.name, "data": rel.data_execucao, "duracao": rel.duracao,
                "total_tabelas": len(rel.tabelas_processadas), "total_inseridos": rel.total_inseridos,
                "status": rel.status, "erros": rel.tabelas_erro, "vazao": vazao,
                "tabelas": {t['nome']: t['registros'] for t in rel.tabelas_processadas},
                "tabelas_detalhe": {t['nome']: {"registros": t['registros'], "tempo": t.get('tempo', 0.0)} for t in rel.tabelas_processadas}
            }
        except Exception as e:
            stats["last_diff"] = {"error": str(e)}

    latest_completo = get_latest_log_file("completo")
    if latest_completo and servicos.EMAIL_PARSER_DISPONIVEL:
        try:
            rel = servicos.RelatorioReplicacao(latest_completo)
            dur_seg = parse_duration_seconds(rel.duracao)
            vazao = round(rel.total_inseridos / dur_seg, 1) if dur_seg > 0 else 0.0
            stats["last_completo"] = {
                "filename": latest_completo.name, "data": rel.data_execucao, "duracao": rel.duracao,
                "total_tabelas": len(rel.tabelas_processadas), "total_inseridos": rel.total_inseridos,
                "status": rel.status, "erros": rel.tabelas_erro, "vazao": vazao,
                "tabelas": {t['nome']: t['registros'] for t in rel.tabelas_processadas},
                "tabelas_detalhe": {t['nome']: {"registros": t['registros'], "tempo": t.get('tempo', 0.0)} for t in rel.tabelas_processadas}
            }
        except Exception as e:
            stats["last_completo"] = {"error": str(e)}

    return stats


@app.get('/api/config')
def get_config():
    """Retorna as configurações atuais do config.ini, mesclando com variáveis de ambiente do OS."""
    cfg = carregar_cfg()

    env_mappings = {
        'origem': {
            'server': 'DB_ORIGEM_SERVER', 'port': 'DB_ORIGEM_PORT', 'database': 'DB_ORIGEM_DB',
            'username': 'DB_ORIGEM_USER', 'password': 'DB_ORIGEM_PASS', 'driver': 'DB_ORIGEM_DRIVER'
        },
        'destino': {
            'server': 'DB_DESTINO_SERVER', 'port': 'DB_DESTINO_PORT', 'database': 'DB_DESTINO_DB',
            'username': 'DB_DESTINO_USER', 'password': 'DB_DESTINO_PASS', 'driver': 'DB_DESTINO_DRIVER'
        },
        'email': {
            'servidor_smtp': 'SMTP_SERVER', 'porta_smtp': 'SMTP_PORT', 'usuario': 'SMTP_USER',
            'senha': 'SMTP_PASS', 'remetente': 'SMTP_FROM', 'destinatarios': 'SMTP_TO'
        },
        'configuracao': {
            'batch_size': 'BATCH_SIZE', 'max_workers': 'MAX_WORKERS',
            'dias_retencao_logs': 'LOGS_RETENTION', 'dias_retry_attempts': 'RETRY_ATTEMPTS'
        }
    }

    data: dict[str, dict] = {}
    for secao, mapeamento in env_mappings.items():
        data[secao] = {}
        for opcao, env_key in mapeamento.items():
            val_ini = cfg.get(secao, opcao, fallback='')
            val_env = os.environ.get(env_key, '')
            val = val_ini if val_ini else val_env
            if opcao in ['password', 'senha']:
                val = MASK_STRING if val else ""
            data[secao][opcao] = val

    for secao in cfg.sections():
        if secao not in data:
            data[secao] = {}
        for opcao in cfg.options(secao):
            if opcao not in data[secao]:
                val = cfg.get(secao, opcao)
                if opcao in ['password', 'senha']:
                    val = MASK_STRING if val else ""
                data[secao][opcao] = val

    if 'tabelas' not in data:
        data['tabelas'] = {}
    if not data['tabelas'].get('tabelas_diff'):
        data['tabelas']['tabelas_diff'] = ",".join(TABELAS_DIFF_DEFAULT)
    if not data['tabelas'].get('tabelas_completo'):
        data['tabelas']['tabelas_completo'] = ",".join(TABELAS_COMPLETO_DEFAULT)

    return data


@app.post('/api/config')
def save_config(req_data: dict):
    """Salva novas configurações de volta no config.ini (com mascaramento seguro)."""
    if not req_data:
        raise ErroAPI("Payload inválido", 400)

    cfg = configparser.ConfigParser()
    cfg.read(DEFAULT_CONFIG_PATH, encoding='utf-8-sig')

    for secao, opcoes in req_data.items():
        if not cfg.has_section(secao):
            cfg.add_section(secao)
        for opcao, valor in opcoes.items():
            if opcao in ['password', 'senha']:
                if valor == MASK_STRING or not valor:
                    continue
            cfg.set(secao, opcao, str(valor))

    try:
        with open(DEFAULT_CONFIG_PATH, 'w', encoding='utf-8-sig') as f:
            cfg.write(f)
        return resposta_sucesso({"message": "Configurações salvas com sucesso."})
    except Exception as e:
        raise ErroAPI(str(e), 500)


@app.post('/api/config/test')
def test_config(body: ConfigTestBody):
    """Testa credenciais de conexão do banco de dados ou SMTP antes de salvar."""
    tipo = body.type
    data = body.data
    cfg = carregar_cfg()

    if tipo in ['origem', 'destino']:
        server = data.get('server', '')
        port = data.get('port', '1433')
        database = data.get('database', '')
        user = data.get('username', '')
        pwd = data.get('password', '')
        driver = data.get('driver', 'ODBC Driver 17 for SQL Server')

        if pwd == MASK_STRING or not pwd:
            pwd = os.environ.get(f'DB_{tipo.upper()}_PASS') or cfg.get(tipo, 'password', fallback='')

        success, msg = testar_conexao_db(server, port, database, user, pwd, driver)
        return resposta_sucesso({"message": msg}, sucesso=success)

    elif tipo == 'smtp':
        server = data.get('servidor_smtp', '')
        port = data.get('porta_smtp', '587')
        user = data.get('usuario', '')
        pwd = data.get('senha', '')

        if pwd == MASK_STRING or not pwd:
            pwd = os.environ.get('SMTP_PASS') or cfg.get('email', 'senha', fallback='')

        success, msg = testar_conexao_smtp(server, port, user, pwd)
        return resposta_sucesso({"message": msg}, sucesso=success)

    else:
        raise ErroAPI(f"Tipo de conexão inválido: '{tipo}'. Use 'origem', 'destino' ou 'smtp'.", 400)


@app.get('/api/docs')
def get_docs():
    """Retorna o conteúdo do arquivo DOCUMENTACAO.md."""
    doc_path = ROOT_DIR / "docs" / "DOCUMENTACAO.md"
    if not doc_path.exists():
        raise ErroAPI("Documentação não encontrada.", 404)
    try:
        with open(doc_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        raise ErroAPI(str(e), 500)


@app.post('/api/run')
def trigger_run(body: RunBody):
    """Dispara uma tarefa de replicação ou validação de integridade em background."""
    import time
    if active_task["running"]:
        raise ErroAPI("Uma tarefa já está em execução.", 409)

    modo = body.modo
    tabela = body.tabela
    smart = body.smart

    if modo not in ['diff', 'completo', 'validador']:
        raise ErroAPI("Modo de execução inválido", 400)

    if modo == 'validador':
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        cmd = [sys.executable, "-u", str(ROOT_DIR / "scripts" / "validador_integridade.py")]
        if tabela:
            cmd.extend(["--tabela", tabela])
        cmd.extend(["--config", str(DEFAULT_CONFIG_PATH)])
        cmd.append("--somente-analise")

        def run_validator():
            try:
                active_task["running"] = True
                active_task["mode"] = "validador"
                active_task["smart"] = False
                active_task["tabela"] = tabela
                active_task["start_time"] = time.time()
                active_task["output"] = ["$ " + " ".join(cmd) + "\n"]
                active_task["exit_code"] = None

                process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(ROOT_DIR), env=env
                )
                while True:
                    line = process.stdout.readline()
                    if not line:
                        break
                    active_task["output"].append(line)
                process.wait()
                active_task["exit_code"] = process.returncode
                active_task["output"].append("\n[VALIDAÇÃO CONCLUÍDA]\n")
            except Exception as e:
                active_task["error"] = str(e)
            finally:
                active_task["running"] = False

        threading.Thread(target=run_validator).start()
    else:
        threading.Thread(target=run_replication_subprocess, args=(modo, tabela, smart)).start()

    label = f"{modo}{' (smart)' if smart else ''}"
    return resposta_sucesso({"message": f"Tarefa '{label}' disparada com sucesso."})


@app.get('/api/run/active')
def get_active_run():
    """Retorna os outputs acumulados e dados da tarefa em execução."""
    import time
    modo_progresso_map = {"diff": "diff", "completo": "completo", "validador": "validacao"}
    output = "".join(active_task["output"])
    modo_progresso = modo_progresso_map.get(active_task["mode"])
    progresso = calcular_progresso_log(modo_progresso, output) if modo_progresso else None
    return {
        "running": active_task["running"],
        "mode": active_task["mode"],
        "smart": active_task["smart"],
        "tabela": active_task["tabela"],
        "duration": round(time.time() - active_task["start_time"], 2) if active_task["running"] and active_task["start_time"] else 0,
        "output": output,
        "exit_code": active_task["exit_code"],
        "error": active_task["error"],
        "progress": progresso
    }


@app.post('/api/audit/start')
def start_audit(body: AuditStartBody):
    if active_audit["running"]:
        raise ErroAPI("Uma auditoria de dados já está rodando.", 409)

    cfg = carregar_cfg()
    import validador_integridade as val

    tabelas_todas = val.carregar_lista_tabelas(cfg, 'tabelas_completo', val.TABELAS_VALIDACAO_DEFAULT)

    if body.tabelas:
        lista_filtro = [t.strip().upper() for t in body.tabelas.split(',') if t.strip()]
        tabelas = [t for t in tabelas_todas if t in lista_filtro]
        if not tabelas:
            tabelas = lista_filtro
    else:
        tabelas = tabelas_todas

    if not tabelas:
        raise ErroAPI("Nenhuma tabela disponível para auditoria.", 400)

    threading.Thread(target=run_realtime_audit, args=(tabelas,)).start()
    return resposta_sucesso({"message": "Auditoria de dados iniciada."})


@app.get('/api/audit/status')
def get_audit_status():
    return active_audit


@app.post('/api/audit/cancel')
def cancel_audit():
    if active_audit["running"]:
        active_audit["running"] = False
        return resposta_sucesso({"message": "Auditoria cancelada pelo usuário.", "cancelled": True})
    return resposta_sucesso({"message": "Nenhuma auditoria em execução.", "cancelled": False})


@app.post('/api/audit/remove-duplicates')
def remove_duplicates(body: RemoveDuplicatesBody):
    """Remove registros duplicados (mesmo R_E_C_N_O_) de uma tabela no banco BI (Destino) e grava log da exclusão."""
    if active_audit["running"]:
        raise ErroAPI("Aguarde a auditoria em andamento terminar antes de remover duplicados.", 409)

    tabela = (body.tabela or '').strip().upper()
    if not tabela:
        raise ErroAPI("Tabela não informada.", 400)

    cfg = carregar_cfg()
    schema_dest = cfg['destino'].get('schema', 'dbo') if cfg.has_section('destino') else 'dbo'

    import validador_integridade as val

    conn_dest = None
    try:
        conn_dest = val.get_conexao(cfg, "destino")

        if not val.tabela_existe(conn_dest, schema_dest, tabela):
            raise ErroAPI(f"Tabela {tabela} não existe no BI (Destino).", 404)

        colunas, linhas = val.remover_duplicados(conn_dest, schema_dest, tabela)
        removidos = len(linhas)

        log_dir = ROOT_DIR / 'logs' / 'duplicados'
        log_dir.mkdir(parents=True, exist_ok=True)
        log_filename = f"duplicados_{tabela}_{datetime.now():%Y%m%d_%H%M%S}.log"
        log_path = log_dir / log_filename

        with open(log_path, 'w', encoding='utf-8-sig') as f:
            f.write(f"Tabela: {tabela}\n")
            f.write(f"Schema (BI): {schema_dest}\n")
            f.write(f"Data/Hora da exclusão: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f.write("Chave de deduplicação: R_E_C_N_O_ (mantida a primeira ocorrência, excedentes removidos)\n")
            f.write(f"Total de registros duplicados removidos: {removidos}\n")
            f.write("-" * 100 + "\n")
            if colunas:
                f.write(" | ".join(colunas) + "\n")
                f.write("-" * 100 + "\n")
                for linha in linhas:
                    f.write(" | ".join(str(v) for v in linha) + "\n")

        logging.info(f"Removidos {removidos} registro(s) duplicado(s) da tabela {tabela} (BI). Log: {log_filename}")

        return resposta_sucesso({
            "removidos": removidos,
            "log_file": log_filename,
            "message": f"{removidos} registro(s) duplicado(s) removido(s) da tabela {tabela}."
        })
    except ErroAPI:
        raise
    except Exception as e:
        raise ErroAPI(str(e), 500)
    finally:
        if conn_dest:
            try: conn_dest.close()
            except Exception: pass


@app.post('/api/speedtest/start')
def start_speedtest():
    """Dispara um teste de velocidade de internet do servidor (download/upload/ping contra a
    Cloudflare) sob demanda, em background — mesmo padrão de /api/run."""
    if active_speedtest["running"]:
        raise ErroAPI("Um teste de velocidade já está em execução.", 409)
    threading.Thread(target=run_speedtest_thread, args=('manual',)).start()
    return resposta_sucesso({"message": "Teste de velocidade iniciado."})


@app.get('/api/speedtest/status')
def get_speedtest_status():
    """Retorna o progresso/resultado do teste de velocidade em andamento (ou do último
    disparado manualmente) — consultado por polling enquanto `running` é true."""
    return active_speedtest


@app.get('/api/schedules')
def get_schedules():
    """Retorna o status de todas as tarefas agendadas no Windows."""
    return get_all_schedule_statuses()


@app.get('/api/schedules/live-log')
def live_schedule_log(modo: str = ''):
    """Retorna as últimas linhas do log mais recente de um modo, para acompanhamento em tempo
    real de tarefas disparadas pelo Agendador de Tarefas do Windows."""
    if modo not in ["diff", "completo", "validacao"]:
        raise ErroAPI("Modo inválido", 400)

    log_file = get_latest_log_file(modo)
    if not log_file:
        return {"name": None, "content": "", "mtime": None}

    try:
        conteudo_completo = log_file.read_text(encoding='utf-8', errors='replace')
        progresso = calcular_progresso_log(modo, conteudo_completo)

        linhas = conteudo_completo.splitlines()
        MAX_LINHAS = 500
        truncado = len(linhas) > MAX_LINHAS
        conteudo = "\n".join(linhas[-MAX_LINHAS:])
        if truncado:
            conteudo = f"[... exibindo apenas as últimas {MAX_LINHAS} linhas ...]\n" + conteudo
        return {
            "name": log_file.name,
            "content": conteudo,
            "mtime": datetime.fromtimestamp(log_file.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            "progress": progresso
        }
    except Exception as e:
        raise ErroAPI(str(e), 500)


@app.post('/api/schedules/action')
def schedule_action(body: ScheduleActionBody):
    """Ativa, desativa, roda ou interrompe tarefas do Windows Task Scheduler."""
    task_name = body.task
    action = body.action

    if task_name not in SCHEDULED_TASK_NAMES:
        raise ErroAPI("Nome da tarefa inválido", 400)

    if action not in ["enable", "disable", "run", "stop"]:
        raise ErroAPI("Ação inválida", 400)

    try:
        if action == "enable":
            cmd = ["schtasks", "/change", "/tn", task_name, "/enable"]
        elif action == "disable":
            cmd = ["schtasks", "/change", "/tn", task_name, "/disable"]
        elif action == "run":
            cmd = ["schtasks", "/run", "/tn", task_name]
        elif action == "stop":
            cmd = ["schtasks", "/end", "/tn", task_name]

        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        servicos._schedule_status_cache["data"] = None
        return resposta_sucesso({"message": f"Ação '{action}' executada com sucesso."})
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.strip() or e.stdout.strip() or str(e)
        raise ErroAPI(f"Erro no Task Scheduler: {err_msg}", 500)


@app.get('/api/logs')
def list_logs():
    """Retorna a lista dos logs mais recentes gerados, organizados por pastas (últimos
    MAX_LOGS_LISTADOS_POR_MODO por modo — ver servicos.py)."""
    log_data: dict[str, list] = {"diff": [], "completo": [], "validacao": []}

    for modo in ["diff", "completo", "validacao"]:
        log_dir = ROOT_DIR / 'logs' / modo
        if log_dir.exists():
            arquivos_com_stat = [(entry, entry.stat()) for entry in _iterar_logs(log_dir)]
            arquivos_com_stat.sort(key=lambda x: x[1].st_mtime, reverse=True)
            for entry, st in arquivos_com_stat[:MAX_LOGS_LISTADOS_POR_MODO]:
                log_data[modo].append({
                    "name": entry.name,
                    "size": st.st_size,
                    "date": datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                })
    return log_data


@app.get('/api/logs/{modo}/{filename}')
def read_log_file(modo: str, filename: str):
    """Lê o conteúdo de um arquivo de log específico."""
    if modo not in ["diff", "completo", "validacao"]:
        raise ErroAPI("Modo inválido", 400)

    # Proteção de segurança contra Path Traversal
    safe_name = Path(filename).name
    log_path = ROOT_DIR / 'logs' / modo / safe_name

    if not log_path.exists():
        raise ErroAPI("Arquivo não encontrado", 404)

    try:
        content = log_path.read_text(encoding='utf-8', errors='replace')
        return {"name": safe_name, "content": content}
    except Exception as e:
        raise ErroAPI(str(e), 500)


# ===========================================================================
# BUILD DE PRODUÇÃO DO ANGULAR (Fase 3a, tarefa 6 do roadmap v2)
# ===========================================================================
# Registrado por ÚLTIMO de propósito: uma rota catch-all (`/{caminho:path}`) casa com QUALQUER
# caminho, então se viesse antes das rotas /api/* ela as esconderia. As rotas internas do
# FastAPI (/docs, /openapi.json, /redoc) não são afetadas porque já foram registradas dentro de
# `FastAPI.__init__()`, antes de qualquer `@app.get/post` deste arquivo rodar.
#
# `ng build` gera em `frontend/dist/frontend/browser/` (Angular 21 sempre usa essa subpasta
# `browser/`, com ou sem SSR). Se essa pasta não existir (dev sem build, só `ng serve` +
# `python main.py`), a rota não é registrada — o backend continua funcionando só como API.
FRONTEND_DIR = ROOT_DIR / 'frontend' / 'dist' / 'frontend' / 'browser'
FRONTEND_INDEX = FRONTEND_DIR / 'index.html'

if FRONTEND_INDEX.exists():
    @app.get('/{caminho:path}')
    def servir_frontend(caminho: str):
        """Serve o build estático do Angular. Roteamento client-side (ex: /painel-geral,
        /configuracoes/bancos) não corresponde a nenhum arquivo físico — cai no fallback de
        sempre servir `index.html`, e o Angular Router decide o resto no navegador."""
        arquivo = FRONTEND_DIR / caminho
        if caminho and arquivo.is_file():
            return FileResponse(arquivo)
        return FileResponse(FRONTEND_INDEX)
else:
    logging.warning(
        f"Build do frontend não encontrado em {FRONTEND_DIR} — rode 'ng build' na pasta "
        "frontend/ pra servir a interface por este processo. Sem o build, só a API responde "
        "(Swagger em /docs); use 'ng serve' à parte pra desenvolvimento."
    )


if __name__ == '__main__':
    import uvicorn
    cfg = carregar_cfg()
    port_ini = cfg.get('configuracao', 'port', fallback='')
    port = int(port_ini) if port_ini else int(os.environ.get('PORT', 5050))
    logging.info(f"Iniciando backend FastAPI na porta {port}... Acesse http://localhost:{port}/docs pro Swagger UI")
    uvicorn.run(app, host=os.environ.get('HOST', '127.0.0.1'), port=port)

