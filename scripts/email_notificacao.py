"""
Módulo para enviar notificações por e-mail dos resultados da replicação.
Formata relatório elegante com status, tabelas processadas e estatísticas.
"""

import smtplib
import logging
import re
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from datetime import datetime
from configparser import ConfigParser


class RelatorioReplicacao:
    """Extrai informações do log de replicação."""
    
    def __init__(self, caminho_log: str):
        self.caminho_log = Path(caminho_log)
        self.modo = ""
        self.data_execucao = ""
        self.tabelas_processadas = []
        self.tabelas_erro = []
        self.total_inseridos = 0
        self.duracao = ""
        self.status = "SUCESSO"
        self.detalhes = []
        
        self._parse_log()
    
    def _parse_log(self):
        """Lê e processa o arquivo de log."""
        if not self.caminho_log.exists():
            self.status = "ERRO"
            self.detalhes.append("Arquivo de log não encontrado.")
            return
        
        try:
            with open(self.caminho_log, 'r', encoding='utf-8') as f:
                conteudo = f.read()
            
            # Validação básica
            if not isinstance(conteudo, str):
                self.status = "ERRO"
                self.detalhes.append(f"Arquivo de log inválido: tipo {type(conteudo)}")
                return
            
            # Extrai modo
            match_modo = re.search(r'Modo: (DIFF|COMPLETO)', conteudo)
            if match_modo:
                self.modo = str(match_modo.group(1)).upper()
            
            # Extrai data (primeira linha com timestamp)
            linhas = conteudo.split('\n')
            if linhas:
                match_data = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', str(linhas[0]))
                if match_data:
                    self.data_execucao = str(match_data.group(1))
            
            # Extrai tabelas processadas, registros e erros linha a linha
            pattern_sucesso = re.compile(
                r'\s+(\w+)(?:\s+\[\w+\])?: finalizada com sucesso\.\s+'
                r'(?:Inseridos/Atualizados|Inseridos|Carregados):\s+(\d+)'
                r'(?:\.?\s+\(Tempo:\s+([\d.,]+)s\))?'
            )
            pattern_sem_novos = re.compile(
                r'\s+(\w+)(?:\s+\[\w+\])?: sem novos registros'
                r'(?:\.?\s+\(Tempo:\s+([\d.,]+)s\))?'
            )
            pattern_erro = re.compile(r'\s+(\w+): ERRO')
            
            for linha in linhas:
                # Tenta sucesso
                match_sucesso = pattern_sucesso.search(linha)
                if match_sucesso:
                    tabela = match_sucesso.group(1).upper()
                    registros = int(match_sucesso.group(2))
                    tempo_str = match_sucesso.group(3)
                    tempo = float(tempo_str.replace(',', '.')) if tempo_str else 0.0
                    
                    self.tabelas_processadas = [t for t in self.tabelas_processadas if t['nome'] != tabela]
                    self.tabelas_processadas.append({'nome': tabela, 'registros': registros, 'tempo': tempo})
                    continue
                
                # Tenta sem novos registros
                match_sem_novos = pattern_sem_novos.search(linha)
                if match_sem_novos:
                    tabela = match_sem_novos.group(1).upper()
                    tempo_str = match_sem_novos.group(2)
                    tempo = float(tempo_str.replace(',', '.')) if tempo_str else 0.0
                    
                    if not any(t['nome'] == tabela for t in self.tabelas_processadas):
                        self.tabelas_processadas.append({'nome': tabela, 'registros': 0, 'tempo': tempo})
                    continue
                
                # Tenta erro
                match_erro = pattern_erro.search(linha)
                if match_erro:
                    tabela = match_erro.group(1).upper()
                    if tabela not in self.tabelas_erro:
                        self.tabelas_erro.append(tabela)
            
            self.total_inseridos = sum(t['registros'] for t in self.tabelas_processadas)
            
            # Extrai duração
            match_duracao = re.search(r'Concluído em ([\d:,.]+)', conteudo)
            if match_duracao:
                self.duracao = str(match_duracao.group(1))
            
            # Define status
            if self.tabelas_erro:
                self.status = "FALHA"
            
        except Exception as e:
            self.status = "ERRO"
            self.detalhes.append(f"Erro ao processar log: {str(e)}")



class NotificadorEmail:
    """Envia notificação por e-mail com relatório de replicação."""
    
    def __init__(self, config: ConfigParser, logger: logging.Logger = None):
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # Tenta acessar a seção de email
        try:
            self.smtp_config = dict(config['email']) if 'email' in config else {}
        except Exception:
            self.smtp_config = {}
    
    def enviar(self, relatorio: RelatorioReplicacao, destinatarios: list = None) -> bool:
        """
        Envia e-mail com o relatório de replicação.
        
        Args:
            relatorio: Instância de RelatorioReplicacao
            destinatarios: Lista de endereços de e-mail (se None, lê da config)
        
        Returns:
            True se enviado com sucesso, False caso contrário
        """
        try:
            # Obtém configurações da seção [email] com fallback para variáveis de ambiente
            email_cfg = self.config['email'] if 'email' in self.config else {}
            servidor_smtp = os.environ.get('SMTP_SERVER') or email_cfg.get('servidor_smtp', '')
            
            porta_smtp_str = os.environ.get('SMTP_PORT') or email_cfg.get('porta_smtp', '587')
            porta_smtp = int(porta_smtp_str) if porta_smtp_str else 587
            
            usuario = os.environ.get('SMTP_USER') or email_cfg.get('usuario', '')
            senha = os.environ.get('SMTP_PASS') or email_cfg.get('senha', '')
            remetente = os.environ.get('SMTP_FROM') or email_cfg.get('remetente', usuario)
            
            if not destinatarios:
                dest_config = os.environ.get('SMTP_TO') or email_cfg.get('destinatarios', '')
                destinatarios = [e.strip() for e in dest_config.split(',') if e.strip()]
            
            if not destinatarios:
                self.logger.warning("Nenhum destinatário configurado para notificação por e-mail.")
                return False
            
            # Cria mensagem
            msg = MIMEMultipart('alternative')
            msg['Subject'] = self._gerar_assunto(relatorio)
            msg['From'] = remetente
            msg['To'] = ', '.join(destinatarios)
            
            # Corpo em HTML
            html = self._gerar_html(relatorio)
            msg.attach(MIMEText(html, 'html', 'utf-8'))
            
            # Envia
            with smtplib.SMTP(servidor_smtp, porta_smtp, timeout=30) as server:
                server.starttls()
                server.login(usuario, senha)
                server.sendmail(remetente, destinatarios, msg.as_string())
            
            self.logger.info(f"E-mail enviado com sucesso para {len(destinatarios)} destinatário(s).")
            return True
        
        except Exception as e:
            self.logger.error(f"Erro ao enviar e-mail: {e}")
            return False
    
    def _gerar_assunto(self, relatorio: RelatorioReplicacao) -> str:
        """Gera linha de assunto do e-mail."""
        emoji_status = "✅" if relatorio.status == "SUCESSO" else "⚠️" if relatorio.status == "FALHA" else "❌"
        return f"{emoji_status} Protheus BI - Replicação {relatorio.modo} - {relatorio.status}"
    
    def _gerar_html(self, relatorio: RelatorioReplicacao) -> str:
        """Gera corpo do e-mail em HTML elegante e formal."""
        
        # Validações seguras
        modo = str(relatorio.modo or "INDEFINIDO").upper()
        status = str(relatorio.status or "ERRO").upper()
        data_exec = str(relatorio.data_execucao or "N/A")
        duracao = str(relatorio.duracao or "N/A")
        total_reg = int(relatorio.total_inseridos or 0)
        
        
        # Configurações dinâmicas para o card de tabelas
        is_diff_sucesso = (modo == "DIFF" and status == "SUCESSO")
        if is_diff_sucesso:
            total_processadas = len(relatorio.tabelas_processadas)
            total_atualizadas = sum(1 for t in relatorio.tabelas_processadas if int(t.get('registros', 0)) > 0)
            tabelas_processadas_label = "Status das Tabelas"
            tabelas_processadas_valor = f"Processado {total_processadas} tabelas, atualizado {total_atualizadas}"
            font_size_tabelas = "13px"
        else:
            tabelas_processadas_label = "Tabelas Processadas"
            tabelas_processadas_valor = str(len(relatorio.tabelas_processadas))
            font_size_tabelas = "24px"

        # Define cores conforme status
        cor_header = "#2E7D32" if status == "SUCESSO" else "#F57C00" if status == "FALHA" else "#C62828"
        cor_status = cor_header
        
        # Formata lista de tabelas
        tabelas_html = ""
        if relatorio.tabelas_processadas:
            is_diff_sucesso = (modo == "DIFF" and status == "SUCESSO")
            
            # Se for DIFF SUCESSO, filtra para mostrar apenas as atualizadas (> 0 registros)
            if is_diff_sucesso:
                tabelas_filtradas = [t for t in relatorio.tabelas_processadas if int(t.get('registros', 0)) > 0]
            else:
                tabelas_filtradas = relatorio.tabelas_processadas

            if tabelas_filtradas:
                tabelas_html = "<table style='width:100%; border-collapse: collapse; margin-top: 15px;'>"
                tabelas_html += "<tr style='background-color: #f5f5f5; border-bottom: 2px solid #ddd;'>"
                tabelas_html += "<th style='padding: 10px; text-align: left; font-weight: bold; color: #333;'>Tabela</th>"
                tabelas_html += "<th style='padding: 10px; text-align: right; font-weight: bold; color: #333;'>Registros</th>"
                tabelas_html += "</tr>"
                
                for tabela in tabelas_filtradas:
                    nome_tab = str(tabela.get('nome', 'N/A'))
                    reg_tab = int(tabela.get('registros', 0))
                    tabelas_html += "<tr style='border-bottom: 1px solid #e0e0e0;'>"
                    tabelas_html += f"<td style='padding: 10px; color: #555;'>{nome_tab}</td>"
                    tabelas_html += f"<td style='padding: 10px; text-align: right; color: #555; font-weight: 500;'>{reg_tab:,}</td>"
                    tabelas_html += "</tr>"
                
                tabelas_html += "</table>"
            else:
                tabelas_html = "<p style='color: #777; font-style: italic; margin-top: 15px;'>Nenhuma tabela teve registros atualizados nesta execução.</p>"
        
        # Formata tabelas com erro
        erros_html = ""
        if relatorio.tabelas_erro:
            tabelas_erro_str = ', '.join([str(t) for t in relatorio.tabelas_erro])
            erros_html = f"""
            <div style='margin-top: 20px; padding: 15px; background-color: #ffebee; border-left: 4px solid #c62828; border-radius: 4px;'>
                <p style='color: #c62828; font-weight: bold; margin: 0 0 10px 0;'>⚠️ Tabelas com Erro ({len(relatorio.tabelas_erro)})</p>
                <p style='color: #555; margin: 0;'>{tabelas_erro_str}</p>
            </div>
            """
        
        html = f"""
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 800px; margin: 0 auto; background-color: #fff; }}
                .header {{ background-color: {cor_header}; color: white; padding: 30px; text-align: center; border-radius: 8px 8px 0 0; }}
                .header h1 {{ margin: 0; font-size: 28px; font-weight: 300; letter-spacing: 1px; }}
                .header p {{ margin: 8px 0 0 0; font-size: 14px; opacity: 0.9; }}
                .content {{ padding: 30px; background-color: #fafafa; border-radius: 0 0 8px 8px; }}
                .status-badge {{
                    display: inline-block;
                    padding: 10px 20px;
                    border-radius: 6px;
                    background-color: {cor_status};
                    color: white;
                    font-weight: bold;
                    font-size: 16px;
                    margin: 15px 0;
                }}
                .info-grid {{
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 20px;
                    margin: 20px 0;
                }}
                .info-box {{
                    padding: 15px;
                    background-color: white;
                    border-radius: 6px;
                    border-left: 4px solid {cor_header};
                }}
                .info-box .label {{ font-size: 12px; color: #999; text-transform: uppercase; letter-spacing: 0.5px; }}
                .info-box .value {{ font-size: 24px; color: {cor_header}; font-weight: bold; margin-top: 5px; }}
                .footer {{ text-align: center; font-size: 12px; color: #999; margin-top: 20px; padding-top: 20px; border-top: 1px solid #e0e0e0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>RELATÓRIO DE REPLICAÇÃO</h1>
                    <p>Protheus BI - {modo}</p>
                </div>
                
                <div class="content">
                    <div class="status-badge">{status}</div>
                    
                    <div class="info-grid">
                        <div class="info-box">
                            <div class="label">Data de Execução</div>
                            <div class="value">{data_exec}</div>
                        </div>
                        <div class="info-box">
                            <div class="label">Duração Total</div>
                            <div class="value">{duracao}</div>
                        </div>
                        <div class="info-box">
                            <div class="label">{tabelas_processadas_label}</div>
                            <div class="value" style="font-size: {font_size_tabelas};">{tabelas_processadas_valor}</div>
                        </div>
                        <div class="info-box">
                            <div class="label">Registros Inseridos</div>
                            <div class="value">{total_reg:,}</div>
                        </div>
                    </div>
                    
                    <h3 style='color: #333; margin-top: 30px; margin-bottom: 10px; border-bottom: 2px solid {cor_header}; padding-bottom: 10px;'>
                        Detalhamento das Tabelas
                    </h3>
                    
                    {tabelas_html}
                    
                    {erros_html}
                    
                    <div class="footer">
                        <p>Este é um e-mail automático. Não responda diretamente a esta mensagem.</p>
                        <p>Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M:%S')}</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html


def enviar_notificacao(log_path: str, config: ConfigParser, logger: logging.Logger = None) -> bool:
    """
    Função simplificada para enviar notificação.
    
    Args:
        log_path: Caminho do arquivo de log gerado
        config: Objeto ConfigParser com configurações
        logger: Logger (opcional)
    
    Returns:
        True se enviado com sucesso
    """
    relatorio = RelatorioReplicacao(log_path)
    notificador = NotificadorEmail(config, logger)
    return notificador.enviar(relatorio)
