import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { PoDialogService, PoModule, PoNotificationService } from '@po-ui/ng-components';

import { ApiService } from '../../core/api.service';
import { ConfigApiBruta, SecaoConexao, SecaoGeral, conexaoVazia, geralVazio, paraNumero } from '../../core/config.model';

type TipoBanco = 'origem' | 'destino';

/**
 * Configurações · Bancos de Dados — uma das 3 páginas em que a antiga tela única de
 * Configurações foi dividida (decisão do usuário em 21/08/2026). Cobre origem (ERP), destino
 * (BI) e as "Opções Gerais" (batch_size/workers/retenção/retry) — agrupadas aqui por serem
 * parâmetros de como a replicação conversa com os bancos, não com e-mail ou tabelas.
 */
@Component({
  selector: 'app-config-bancos',
  standalone: true,
  imports: [CommonModule, FormsModule, PoModule],
  templateUrl: './config-bancos.html',
  styleUrl: './config-bancos.scss',
})
export class ConfigBancos implements OnInit {
  origem: SecaoConexao = conexaoVazia();
  destino: SecaoConexao = conexaoVazia();
  geral: SecaoGeral = geralVazio();

  carregando = true;
  salvando = false;
  erro: string | null = null;

  testando: Record<TipoBanco, boolean> = { origem: false, destino: false };

  constructor(
    private readonly api: ApiService,
    private readonly dialog: PoDialogService,
    private readonly notification: PoNotificationService
  ) {}

  ngOnInit(): void {
    this.carregarConfig();
  }

  salvar(): void {
    this.salvando = true;
    this.api.post<{ message: string }>('/api/config', { origem: this.origem, destino: this.destino, configuracao: this.geral }).subscribe({
      next: (resultado) => {
        this.salvando = false;
        this.notification.success(resultado.message);
        this.carregarConfig();
      },
      error: (e: Error) => {
        this.salvando = false;
        this.notification.error(e.message);
      },
    });
  }

  testarConexao(tipo: TipoBanco): void {
    this.testando[tipo] = true;
    const dados = tipo === 'origem' ? this.origem : this.destino;

    this.api.postComEnvelope<{ message: string }>('/api/config/test', { type: tipo, data: dados }).subscribe({
      next: (resposta) => {
        this.testando[tipo] = false;
        if (resposta.success) {
          this.notification.success(resposta.data.message);
        } else {
          this.dialog.alert({ title: 'Erro no Teste de Conexão', message: resposta.data.message });
        }
      },
      error: (e: Error) => {
        this.testando[tipo] = false;
        this.notification.error(e.message);
      },
    });
  }

  private carregarConfig(): void {
    this.api.get<ConfigApiBruta>('/api/config').subscribe({
      next: (dados) => {
        this.origem = { ...conexaoVazia(), ...dados.origem };
        this.destino = { ...conexaoVazia(), ...dados.destino };
        this.geral = {
          batch_size: paraNumero(dados.configuracao?.batch_size, geralVazio().batch_size),
          max_workers: paraNumero(dados.configuracao?.max_workers, geralVazio().max_workers),
          dias_retencao_logs: paraNumero(dados.configuracao?.dias_retencao_logs, geralVazio().dias_retencao_logs),
          dias_retry_attempts: paraNumero(dados.configuracao?.dias_retry_attempts, geralVazio().dias_retry_attempts),
        };
        this.carregando = false;
      },
      error: (e: Error) => {
        this.erro = e.message;
        this.carregando = false;
      },
    });
  }
}
