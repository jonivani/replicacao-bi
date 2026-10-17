import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { PoDialogService, PoModule, PoNotificationService, PoTableAction, PoTableColumn } from '@po-ui/ng-components';

import { ApiService } from '../../core/api.service';
import { ConfigApiBruta, SecaoEmail, emailVazio } from '../../core/config.model';

interface LinhaDestinatario {
  email: string;
}

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function paraListaEmails(csv: string | undefined): string[] {
  return (csv ?? '')
    .split(',')
    .map((email) => email.trim())
    .filter((email) => email.length > 0);
}

/**
 * Configurações · E-mail — segunda das 3 páginas em que a tela única de Configurações foi
 * dividida (decisão do usuário em 21/08/2026). Cobre o SMTP de notificações e os destinatários.
 *
 * Destinatários redesenhado em 22/08/2026 (decisão do usuário: "mesma lógica aplicada em
 * Tabelas") — o campo `destinatarios` também é um CSV no `config.ini` (igual
 * `tabelas_diff`/`tabelas_completo`), então virou um formulário de adicionar (e-mail + botão)
 * + uma `po-table` visual com ação de remover por linha, em vez de um único campo de texto com
 * e-mails separados por vírgula. O formato persistido não muda — `email.destinatarios` continua
 * uma string CSV; `destinatarios` (array) é só o estado em memória da tela.
 */
@Component({
  selector: 'app-config-email',
  standalone: true,
  imports: [CommonModule, FormsModule, PoModule],
  templateUrl: './config-email.html',
  styleUrl: './config-email.scss',
})
export class ConfigEmail implements OnInit {
  email: SecaoEmail = emailVazio();
  destinatarios: string[] = [];
  linhasDestinatarios: LinhaDestinatario[] = [];
  novoDestinatario = '';

  carregando = true;
  salvando = false;
  testando = false;
  erro: string | null = null;

  readonly colunas: Array<PoTableColumn> = [{ property: 'email', label: 'E-mail', type: 'string' }];

  readonly acoes: Array<PoTableAction> = [
    { label: 'Remover', type: 'danger', action: (linha: LinhaDestinatario) => this.removerDestinatario(linha.email) },
  ];

  constructor(
    private readonly api: ApiService,
    private readonly dialog: PoDialogService,
    private readonly notification: PoNotificationService
  ) {}

  ngOnInit(): void {
    this.carregarConfig();
  }

  adicionarDestinatario(): void {
    const valor = this.novoDestinatario.trim();
    if (!valor) return;
    if (!EMAIL_REGEX.test(valor)) {
      this.notification.warning('Informe um e-mail válido.');
      return;
    }
    if (!this.destinatarios.includes(valor)) {
      this.destinatarios = [...this.destinatarios, valor].sort();
      this.atualizarLinhas();
    }
    this.novoDestinatario = '';
  }

  removerDestinatario(email: string): void {
    this.destinatarios = this.destinatarios.filter((e) => e !== email);
    this.atualizarLinhas();
  }

  salvar(): void {
    this.salvando = true;
    const payload: SecaoEmail = { ...this.email, destinatarios: this.destinatarios.join(',') };
    this.api.post<{ message: string }>('/api/config', { email: payload }).subscribe({
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

  testarConexao(): void {
    this.testando = true;

    this.api.postComEnvelope<{ message: string }>('/api/config/test', { type: 'smtp', data: this.email }).subscribe({
      next: (resposta) => {
        this.testando = false;
        if (resposta.success) {
          this.notification.success(resposta.data.message);
        } else {
          this.dialog.alert({ title: 'Erro no Teste de Conexão', message: resposta.data.message });
        }
      },
      error: (e: Error) => {
        this.testando = false;
        this.notification.error(e.message);
      },
    });
  }

  private atualizarLinhas(): void {
    this.linhasDestinatarios = this.destinatarios.map((email) => ({ email }));
  }

  private carregarConfig(): void {
    this.api.get<ConfigApiBruta>('/api/config').subscribe({
      next: (dados) => {
        this.email = { ...emailVazio(), ...dados.email };
        this.destinatarios = paraListaEmails(dados.email?.destinatarios);
        this.atualizarLinhas();
        this.carregando = false;
      },
      error: (e: Error) => {
        this.erro = e.message;
        this.carregando = false;
      },
    });
  }
}
