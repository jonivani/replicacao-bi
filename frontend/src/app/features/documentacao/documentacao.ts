import { CommonModule } from '@angular/common';
import { Component, ElementRef, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { PoModule } from '@po-ui/ng-components';
import { Subscription } from 'rxjs';

import { ApiService } from '../../core/api.service';
import { dividirDocumentacao, SecaoDocumentacao } from '../../core/markdown-simples';

@Component({
  selector: 'app-documentacao',
  standalone: true,
  imports: [CommonModule, FormsModule, PoModule],
  templateUrl: './documentacao.html',
  styleUrl: './documentacao.scss',
})
export class Documentacao implements OnInit, OnDestroy {
  introducaoHtml = '';
  secoes: SecaoDocumentacao[] = [];
  secoesFiltradas: SecaoDocumentacao[] = [];
  busca = '';
  carregando = true;
  erro: string | null = null;
  private requisicao?: Subscription;

  constructor(private readonly api: ApiService, private readonly elemento: ElementRef<HTMLElement>) {}

  ngOnInit(): void {
    this.carregar();
  }

  ngOnDestroy(): void {
    this.requisicao?.unsubscribe();
  }

  carregar(): void {
    this.requisicao?.unsubscribe();
    this.carregando = true;
    this.erro = null;
    this.requisicao = this.api.get<{ content: string }>('/api/docs').subscribe({
      next: ({ content }) => {
        if (!content.trim()) {
          this.erro = 'O manual está vazio. Verifique o arquivo docs/DOCUMENTACAO.md no servidor.';
          this.introducaoHtml = '';
          this.secoes = [];
        } else {
          const documento = dividirDocumentacao(content);
          this.introducaoHtml = documento.introducao;
          this.secoes = documento.secoes;
        }
        this.filtrar();
        this.carregando = false;
      },
      error: (e: Error) => {
        this.erro = e.message;
        this.carregando = false;
      },
    });
  }

  filtrar(): void {
    const termos = normalizar(this.busca).split(/\s+/).filter(Boolean);
    this.secoesFiltradas = this.secoes.filter(secao => termos.every(termo => normalizar(secao.markdown).includes(termo)));
  }

  limparBusca(): void {
    this.busca = '';
    this.filtrar();
  }

  navegar(id: string): void {
    const destino = this.elemento.nativeElement.querySelector<HTMLElement>(`#${id}`);
    destino?.scrollIntoView({ block: 'start' });
    destino?.focus({ preventScroll: true });
  }
}

function normalizar(texto: string): string {
  return texto.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}
