import { Injectable } from '@angular/core';
import { PoMultiselectOption } from '@po-ui/ng-components';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';

import { ApiService } from './api.service';
import { ConfigApiBruta, paraListaTabelas } from './config.model';

/**
 * Lista de tabelas configuradas (união de `tabelas_diff`/`tabelas_completo`), pronta pro
 * `po-options` de um `po-multiselect` — usada pelo card "Replicar Tabelas Específicas" (Central
 * de Carga) e por "Auditar Selecionadas" (Auditoria de Dados), decisão do usuário em 21/08/2026.
 * Cada tela mantém sua própria seleção local (`tabelasSelecionadas`); só a LISTA de opções vem
 * compartilhada daqui — não existe um estado de seleção global entre telas como no v1 (lá era
 * uma única página com uma variável JS global `selectedTables`; aqui, escolher tabelas na
 * Central de Carga não afeta o que está selecionado na Auditoria, e vice-versa).
 */
@Injectable({ providedIn: 'root' })
export class TabelasService {
  constructor(private readonly api: ApiService) {}

  listar(): Observable<PoMultiselectOption[]> {
    return this.api.get<ConfigApiBruta>('/api/config').pipe(
      map((dados) => {
        const nomes = new Set([...paraListaTabelas(dados.tabelas?.tabelas_diff), ...paraListaTabelas(dados.tabelas?.tabelas_completo)]);
        return Array.from(nomes)
          .sort()
          .map((nome) => ({ value: nome, label: nome }));
      })
    );
  }
}
