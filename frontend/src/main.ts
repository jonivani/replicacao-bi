import { registerLocaleData } from '@angular/common';
import localePt from '@angular/common/locales/pt';
import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { App } from './app/app';

// Angular só embute dados de locale en-US por padrão — sem registrar pt-BR, qualquer pipe
// (DecimalPipe, DatePipe, CurrencyPipe) formatado com 'pt-BR' derruba a view inteira com
// NG0701 "Missing locale data". Achado ao testar o Painel Geral no navegador de verdade.
registerLocaleData(localePt);

bootstrapApplication(App, appConfig)
  .catch((err) => console.error(err));
