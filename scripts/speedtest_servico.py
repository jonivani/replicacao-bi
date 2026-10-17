"""
Teste de velocidade de internet do servidor — mesma ideia do fast.com: mede ping, download e
upload contra a rede pública da Cloudflare (`speed.cloudflare.com/__down` e `/__up`, os mesmos
endpoints que alimentam o speed.cloudflare.com, sem autenticação), usando só `urllib` da stdlib
— sem precisar de nenhuma biblioteca nova no requirements.txt (o `speedtest-cli` do PyPI está
sem manutenção desde 2020 e não é confiável no Python 3.14 usado aqui).

Serve pra diagnosticar se a lentidão da internet do servidor está causando/agravando problemas
na transferência de dados.

Usado em dois lugares, propositalmente sem depender de nenhum dos dois (evita import circular,
já que `servicos.py` importa constantes de `replicacao.py`):
- `servicos.py` (via `active_speedtest`) — teste sob demanda disparado pelo card do Painel Geral.
- `replicacao.py` — teste automático ao final de cada execução DIFF.
"""

import json
import logging
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
RESULTADO_PATH = ROOT_DIR / 'logs' / 'speedtest' / 'ultimo_resultado.json'

_HOST = "speed.cloudflare.com"
DOWNLOAD_BYTES = 10_000_000   # 10 MB — estimativa razoável sem pesar no link
UPLOAD_BYTES = 3_000_000      # 3 MB
_CHUNK = 131_072              # 128 KB por leitura, pra poder reportar progresso ao vivo

# Sem um User-Agent de navegador, a Cloudflare responde 403 Forbidden ao urllib (o UA padrão
# "Python-urllib/x.y" é tratado como tráfego automatizado) — confirmado testando manualmente.
_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ReplicacaoBI-Speedtest/1.0'}


def _medir_ping(amostras: int = 4) -> Optional[float]:
    """Round-trip médio (ms) de `amostras` requisições ao Cloudflare (bytes=0, resposta mínima)."""
    tempos = []
    for _ in range(amostras):
        inicio = time.perf_counter()
        try:
            req = urllib.request.Request(f"https://{_HOST}/__down?bytes=0", headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
            tempos.append((time.perf_counter() - inicio) * 1000)
        except Exception:
            continue
    return round(sum(tempos) / len(tempos), 1) if tempos else None


def _medir_download(bytes_alvo: int = DOWNLOAD_BYTES, progress_cb: Optional[Callable[[int, float], None]] = None) -> Optional[float]:
    """Baixa `bytes_alvo` do Cloudflare e devolve a taxa em Mbps.

    `progress_cb(bytes_ate_agora, segundos_decorridos)`, quando passado, é chamado a cada chunk
    lido — usado pro card mostrar o número subindo ao vivo enquanto mede, igual ao fast.com, em
    vez de só aparecer no final.
    """
    inicio = time.perf_counter()
    total = 0
    req = urllib.request.Request(f"https://{_HOST}/__down?bytes={bytes_alvo}", headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        while True:
            chunk = resp.read(_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if progress_cb:
                progress_cb(total, time.perf_counter() - inicio)
    duracao = time.perf_counter() - inicio
    if duracao <= 0 or total == 0:
        return None
    return round((total * 8 / 1_000_000) / duracao, 2)


def _medir_upload(bytes_alvo: int = UPLOAD_BYTES, progress_cb: Optional[Callable[[int, float], None]] = None) -> Optional[float]:
    """Envia `bytes_alvo` aleatórios ao Cloudflare e devolve a taxa em Mbps.

    `urllib` não expõe progresso de envio chunk a chunk como no download — `progress_cb`, quando
    passado, é chamado uma única vez ao final com o total, só pra o card "assentar" no valor
    final em vez de ficar parado em zero até a chamada inteira terminar.
    """
    payload = os.urandom(bytes_alvo)
    inicio = time.perf_counter()
    req = urllib.request.Request(
        f"https://{_HOST}/__up", data=payload, method='POST',
        headers={**_HEADERS, 'Content-Type': 'application/octet-stream'}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
    duracao = time.perf_counter() - inicio
    if progress_cb:
        progress_cb(bytes_alvo, duracao)
    if duracao <= 0:
        return None
    return round((bytes_alvo * 8 / 1_000_000) / duracao, 2)


def executar_speedtest(origem: str = 'manual', progress_cb: Optional[Callable[[str, int, float], None]] = None) -> dict:
    """Mede ping, download e upload, nesta ordem. `origem` registra se a medição veio do card
    (clique manual) ou do fim de uma execução DIFF (`automatico_diff`).

    `progress_cb(fase, bytes_ate_agora, segundos)`, quando passado, é repassado às medições de
    download/upload com `fase` em ('download', 'upload') — usado por
    `servicos.run_speedtest_thread` pra expor o progresso em `active_speedtest`, consultável por
    polling enquanto o teste ainda está rodando.
    """
    resultado = {
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "origem": origem,
        "ping_ms": None,
        "download_mbps": None,
        "upload_mbps": None,
        "error": None,
    }
    try:
        resultado["ping_ms"] = _medir_ping()
        resultado["download_mbps"] = _medir_download(
            progress_cb=(lambda b, s: progress_cb('download', b, s)) if progress_cb else None
        )
        resultado["upload_mbps"] = _medir_upload(
            progress_cb=(lambda b, s: progress_cb('upload', b, s)) if progress_cb else None
        )
    except Exception as e:
        resultado["error"] = str(e)
    return resultado


def salvar_resultado(resultado: dict) -> None:
    """Persiste a última medição em logs/speedtest/ultimo_resultado.json — mesmo padrão de
    logs/validacao/ultimo_resultado.json, lido pelo /api/status (backend/main.py) pra alimentar
    o card do Painel Geral."""
    try:
        RESULTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULTADO_PATH.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        logging.error(f"Falha ao salvar resultado do teste de velocidade: {e}")


def carregar_ultimo_resultado() -> Optional[dict]:
    if not RESULTADO_PATH.exists():
        return None
    try:
        return json.loads(RESULTADO_PATH.read_text(encoding='utf-8-sig'))
    except Exception as e:
        return {"error": str(e)}


def resultado_esta_recente(minutos: int) -> bool:
    """Usado por replicacao.py pra não medir a cada DIFF (a tarefa roda a cada 5 min — ver
    docs/instalar_tarefas.ps1) — só testa de novo se a última medição salva tiver mais de
    `minutos` de idade."""
    ultimo = carregar_ultimo_resultado()
    if not ultimo or not ultimo.get("timestamp"):
        return False
    try:
        ts = datetime.strptime(ultimo["timestamp"], '%Y-%m-%d %H:%M:%S')
    except Exception:
        return False
    return (datetime.now() - ts).total_seconds() < minutos * 60
