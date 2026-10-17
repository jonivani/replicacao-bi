import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { version } from '../../package.json';

import {
  PoMenuItem,
  PoMenuModule,
  PoPageModule,
  PoThemeA11yEnum,
  PoThemeService,
  PoThemeTypeEnum,
  PoToolbarModule,
} from '@po-ui/ng-components';

import { TEMA_REPLICACAO_BI } from './core/theme';

@Component({
  selector: 'app-root',
  imports: [CommonModule, RouterOutlet, PoToolbarModule, PoMenuModule, PoPageModule],
  templateUrl: './app.html',
  styleUrls: ['./app.css'],
})
export class App {
  readonly versao = version;

  constructor(private readonly themeService: PoThemeService) {
    // AA em vez de AAA (decisão do usuário em 22/08/2026): contorno de foco fica 2px (era 4px)
    // e libera o tamanho `small` pros componentes de formulário. `setA11yDefaultSizeSmall(true)`
    // é chamado ANTES de `setTheme()` de propósito — é a ordem recomendada pela própria doc do
    // PO-UI pra esse tamanho ser aplicado globalmente (senão cada componente cai no tamanho
    // padrão dele, mesmo com o nível AA ativo).
    this.themeService.setA11yDefaultSizeSmall(true);
    this.themeService.setTheme(TEMA_REPLICACAO_BI, PoThemeTypeEnum.light, PoThemeA11yEnum.AA);
  }

  // Fase 3b, tarefa 5: todas as telas do v1 migradas (ver docs/ROADMAP_V2.md). Configurações
  // virou um submenu com 3 filhas em 21/08/2026 (decisão do usuário) em vez de uma tela única.
  readonly menus: Array<PoMenuItem> = [
    { label: 'Painel Geral', link: '/painel-geral', icon: 'an an-gauge' },
    { label: 'Central de Carga', link: '/central-carga', icon: 'an an-play' },
    { label: 'Auditoria de Dados', link: '/auditoria', icon: 'an an-magnifying-glass' },
    { label: 'Agendador', link: '/agendador', icon: 'an an-calendar' },
    {
      label: 'Configurações',
      icon: 'an an-gear',
      subItems: [
        { label: 'Bancos de Dados', link: '/configuracoes/bancos' },
        { label: 'E-mail', link: '/configuracoes/email' },
        { label: 'Tabelas', link: '/configuracoes/tabelas' },
      ],
    },
    { label: 'Histórico de Logs', link: '/logs', icon: 'an an-file-text' },
    { label: 'Documentação', link: '/documentacao', icon: 'an an-book-open' },
  ];
}
