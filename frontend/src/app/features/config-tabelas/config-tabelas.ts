import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { PoModule, PoNotificationService, PoTableAction, PoTableColumn } from '@po-ui/ng-components';

import { ApiService } from '../../core/api.service';
import { ConfigApiBruta, paraListaTabelas } from '../../core/config.model';

interface LinhaTabela {
  tabela: string;
  diff: boolean;
  completo: boolean;
}

/** Junta as duas listas separadas (`tabelas_diff`/`tabelas_completo`) numa única lista de linhas
 * — uma por tabela, com uma flag por modo — pra alimentar a tabela visual. */
function unificarLinhas(diff: string[], completo: string[]): LinhaTabela[] {
  const nomes = new Set([...diff, ...completo]);
  const diffSet = new Set(diff);
  const completoSet = new Set(completo);
  return Array.from(nomes)
    .sort()
    .map((tabela) => ({ tabela, diff: diffSet.has(tabela), completo: completoSet.has(tabela) }));
}

/**
 * Configurações · Tabelas — mapeamento dinâmico de tabelas Protheus por modo.
 *
 * Redesenhado em 21/08/2026 (decisão do usuário): as duas caixas de texto com nomes separados
 * por vírgula viraram um formulário de adicionar (nome da tabela + marcação de DIFF e/ou
 * COMPLETO) e uma tabela visual com uma ação por linha pra tirar a tabela de cada modo. O
 * formato salvo no `config.ini` continua o mesmo de sempre — `tabelas_diff`/`tabelas_completo`
 * como string separada por vírgula (`POST /api/config`) — só a edição ficou mais direta; `linhas`
 * é o estado de verdade em memória, convertido de volta pras duas strings só na hora de salvar.
 */
@Component({
  selector: 'app-config-tabelas',
  standalone: true,
  imports: [CommonModule, FormsModule, PoModule],
  templateUrl: './config-tabelas.html',
  styleUrl: './config-tabelas.scss',
})
export class ConfigTabelas implements OnInit {
  linhas: LinhaTabela[] = [];

  novaTabela = '';
  novaDiff = true;
  novaCompleto = true;

  carregando = true;
  salvando = false;
  erro: string | null = null;

  readonly colunas: Array<PoTableColumn> = [
    { property: 'tabela', label: 'Tabela', type: 'string' },
    { property: 'diff', label: 'DIFF', type: 'boolean', boolean: { trueLabel: 'Ativo', falseLabel: '-' } },
    { property: 'completo', label: 'COMPLETO', type: 'boolean', boolean: { trueLabel: 'Ativo', falseLabel: '-' } },
  ];

  readonly acoes: Array<PoTableAction> = [
    {
      label: 'Tirar do DIFF',
      type: 'danger',
      action: (linha: LinhaTabela) => this.removerDoModo(linha, 'diff'),
      visible: (linha: LinhaTabela) => linha.diff,
    },
    {
      label: 'Tirar do COMPLETO',
      type: 'danger',
      action: (linha: LinhaTabela) => this.removerDoModo(linha, 'completo'),
      visible: (linha: LinhaTabela) => linha.completo,
    },
  ];

  constructor(
    private readonly api: ApiService,
    private readonly notification: PoNotificationService
  ) {}

  ngOnInit(): void {
    this.carregarConfig();
  }

  adicionar(): void {
    const nome = this.novaTabela.trim().toUpperCase();
    if (!nome) return;
    if (!this.novaDiff && !this.novaCompleto) {
      this.notification.warning('Marque pelo menos um modo (DIFF ou COMPLETO) antes de adicionar.');
      return;
    }

    const existente = this.linhas.find((l) => l.tabela === nome);
    if (existente) {
      existente.diff = existente.diff || this.novaDiff;
      existente.completo = existente.completo || this.novaCompleto;
    } else {
      this.linhas.push({ tabela: nome, diff: this.novaDiff, completo: this.novaCompleto });
      this.linhas.sort((a, b) => a.tabela.localeCompare(b.tabela));
    }
    this.linhas = [...this.linhas];
    this.novaTabela = '';
  }

  removerDoModo(linha: LinhaTabela, modo: 'diff' | 'completo'): void {
    linha[modo] = false;
    this.linhas = linha.diff || linha.completo ? [...this.linhas] : this.linhas.filter((l) => l !== linha);
  }

  salvar(): void {
    this.salvando = true;
    const tabelas_diff = this.linhas
      .filter((l) => l.diff)
      .map((l) => l.tabela)
      .join(',');
    const tabelas_completo = this.linhas
      .filter((l) => l.completo)
      .map((l) => l.tabela)
      .join(',');

    this.api.post<{ message: string }>('/api/config', { tabelas: { tabelas_diff, tabelas_completo } }).subscribe({
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

  private carregarConfig(): void {
    this.api.get<ConfigApiBruta>('/api/config').subscribe({
      next: (dados) => {
        this.linhas = unificarLinhas(paraListaTabelas(dados.tabelas?.tabelas_diff), paraListaTabelas(dados.tabelas?.tabelas_completo));
        this.carregando = false;
      },
      error: (e: Error) => {
        this.erro = e.message;
        this.carregando = false;
      },
    });
  }
}
