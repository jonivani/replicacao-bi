import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { PoModule, PoTableAction, PoTableColumn } from '@po-ui/ng-components';

import { ApiService } from '../../core/api.service';

type Modo = 'diff' | 'completo' | 'validacao';

interface LogArquivo {
  name: string;
  size: number;
  date: string;
}

interface LogsApi {
  diff: LogArquivo[];
  completo: LogArquivo[];
  validacao: LogArquivo[];
}

interface LinhaLog {
  modo: Modo;
  name: string;
  date: string;
  tamanho: string;
}

interface LogAberto {
  modo: Modo;
  name: string;
  content: string;
}

/**
 * Histórico de Logs — sexta tela migrada na Fase 3b (roadmap v2), depois de Configurações.
 * Lista os arquivos de log das 3 rotinas (`GET /api/logs`) em 3 tabelas lado a lado, cada uma
 * com uma ação "Visualizar" que busca o conteúdo do arquivo (`GET /api/logs/{modo}/{filename}`)
 * e mostra num console abaixo — mesmo padrão do `viewLogFile`/`closeLogViewer` do v1, só que sem
 * manipulação direta do DOM.
 */
@Component({
  selector: 'app-logs',
  standalone: true,
  imports: [CommonModule, PoModule],
  templateUrl: './logs.html',
  styleUrl: './logs.scss',
})
export class Logs implements OnInit {
  carregando = true;
  erro: string | null = null;

  // Arrays computados uma vez ao carregar (não em métodos chamados direto no template) — um
  // método de template devolvendo array novo a cada ciclo de detecção de mudanças faz o
  // `po-table` reagir a uma referência nova em `[p-items]` toda vez, travando a aba inteira
  // num loop de re-render (achado ao vivo: a página parava de responder e até um screenshot
  // do Playwright dava timeout — não aparecia em `ng build`, só em uso real).
  linhasDiff: LinhaLog[] = [];
  linhasCompleto: LinhaLog[] = [];
  linhasValidacao: LinhaLog[] = [];

  logAberto: LogAberto | null = null;
  carregandoConteudo = false;

  readonly colunas: Array<PoTableColumn> = [
    { property: 'name', label: 'Arquivo', type: 'string' },
    { property: 'date', label: 'Data', type: 'string' },
    { property: 'tamanho', label: 'Tamanho', type: 'string' },
  ];

  readonly acoes: Array<PoTableAction> = [
    { label: 'Visualizar', action: (linha: LinhaLog) => this.abrirLog(linha.modo, linha.name) },
  ];

  constructor(private readonly api: ApiService) {}

  ngOnInit(): void {
    this.api.get<LogsApi>('/api/logs').subscribe({
      next: (dados) => {
        this.linhasDiff = paraLinhas('diff', dados.diff);
        this.linhasCompleto = paraLinhas('completo', dados.completo);
        this.linhasValidacao = paraLinhas('validacao', dados.validacao);
        this.carregando = false;
      },
      error: (e: Error) => {
        this.erro = e.message;
        this.carregando = false;
      },
    });
  }

  fecharLog(): void {
    this.logAberto = null;
  }

  private abrirLog(modo: Modo, filename: string): void {
    this.carregandoConteudo = true;
    this.logAberto = { modo, name: filename, content: '' };
    setTimeout(() => document.querySelector('.log-viewer')?.scrollIntoView({ behavior: 'smooth' }), 0);

    this.api.get<{ name: string; content: string }>(`/api/logs/${modo}/${encodeURIComponent(filename)}`).subscribe({
      next: (dados) => {
        this.logAberto = { modo, name: dados.name, content: dados.content };
        this.carregandoConteudo = false;
      },
      error: (e: Error) => {
        this.logAberto = { modo, name: filename, content: `Erro ao ler arquivo: ${e.message}` };
        this.carregandoConteudo = false;
      },
    });
  }
}

function paraLinhas(modo: Modo, arquivos: LogArquivo[]): LinhaLog[] {
  return arquivos.map((log) => ({ modo, name: log.name, date: log.date, tamanho: `${(log.size / 1024).toFixed(1)} KB` }));
}
