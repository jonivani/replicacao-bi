import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { ApiService } from '../../core/api.service';
import { Documentacao } from './documentacao';

const manual = '# Manual\n\n## E-mail\nConfiguração SMTP\n\n## Auditoria\nRemover duplicados';

describe('Documentacao', () => {
  let resposta = of({ content: manual });
  const api = { get: () => resposta };

  beforeEach(async () => {
    resposta = of({ content: manual });
    await TestBed.configureTestingModule({
      imports: [Documentacao],
      providers: [{ provide: ApiService, useValue: api }],
    }).compileComponents();
  });

  it('exibe manual, índice e busca por conteúdo sem exigir acentos', () => {
    const fixture = TestBed.createComponent(Documentacao);
    fixture.detectChanges();
    const pagina = fixture.componentInstance;
    expect(fixture.nativeElement.querySelectorAll('.doc-indice button').length).toBe(2);
    pagina.busca = 'configuracao smtp';
    pagina.filtrar();
    fixture.detectChanges();
    expect(pagina.secoesFiltradas.map(s => s.titulo)).toEqual(['E-mail']);
    expect(fixture.nativeElement.querySelectorAll('.doc-secao').length).toBe(1);
    pagina.limparBusca();
    expect(pagina.secoesFiltradas.length).toBe(2);
  });

  it('exibe estado vazio e permite restaurar o manual', () => {
    const fixture = TestBed.createComponent(Documentacao);
    fixture.detectChanges();
    fixture.componentInstance.busca = 'inexistente';
    fixture.componentInstance.filtrar();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.doc-vazio').textContent).toContain('Nenhum assunto encontrado');
    fixture.componentInstance.limparBusca();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.doc-vazio')).toBeNull();
  });

  it('trata falha da API e permite tentar novamente', () => {
    resposta = throwError(() => new Error('Servidor indisponível'));
    const fixture = TestBed.createComponent(Documentacao);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[role="alert"]').textContent).toContain('Servidor indisponível');
    resposta = of({ content: manual });
    fixture.componentInstance.carregar();
    fixture.detectChanges();
    expect(fixture.componentInstance.erro).toBeNull();
    expect(fixture.nativeElement.querySelectorAll('.doc-secao').length).toBe(2);
  });

  it('informa quando o arquivo do manual está vazio', () => {
    resposta = of({ content: '  ' });
    const fixture = TestBed.createComponent(Documentacao);
    fixture.detectChanges();
    expect(fixture.componentInstance.erro).toContain('manual está vazio');
    expect(fixture.componentInstance.carregando).toBe(false);
  });
});
