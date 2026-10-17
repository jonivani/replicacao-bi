import { Routes } from '@angular/router';

import { Agendador } from './features/agendador/agendador';
import { Auditoria } from './features/auditoria/auditoria';
import { CentralCarga } from './features/central-carga/central-carga';
import { ConfigBancos } from './features/config-bancos/config-bancos';
import { ConfigEmail } from './features/config-email/config-email';
import { ConfigTabelas } from './features/config-tabelas/config-tabelas';
import { Documentacao } from './features/documentacao/documentacao';
import { Logs } from './features/logs/logs';
import { PainelGeral } from './features/painel-geral/painel-geral';

// Fase 3b (roadmap v2), tarefa 5 — todas as telas do v1 migradas: Painel Geral, Central de
// Carga, Auditoria de Dados, Agendador, Configurações (dividida em 3 sub-telas — decisão do
// usuário em 21/08/2026), Histórico de Logs e Documentação.
export const routes: Routes = [
  { path: '', redirectTo: 'painel-geral', pathMatch: 'full' },
  { path: 'painel-geral', component: PainelGeral },
  { path: 'central-carga', component: CentralCarga },
  { path: 'auditoria', component: Auditoria },
  { path: 'agendador', component: Agendador },
  { path: 'configuracoes', redirectTo: 'configuracoes/bancos', pathMatch: 'full' },
  { path: 'configuracoes/bancos', component: ConfigBancos },
  { path: 'configuracoes/email', component: ConfigEmail },
  { path: 'configuracoes/tabelas', component: ConfigTabelas },
  { path: 'logs', component: Logs },
  { path: 'documentacao', component: Documentacao },
];
