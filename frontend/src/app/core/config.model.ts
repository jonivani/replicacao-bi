/**
 * Formas compartilhadas entre as 3 telas de Configurações (Bancos de Dados, E-mail, Tabelas —
 * decisão do usuário em 21/08/2026: dividir a tela única em páginas específicas por assunto,
 * agrupadas num submenu "Configurações"). Cada tela consulta o mesmo `GET /api/config` inteiro e
 * usa só a fatia que lhe interessa; salvar manda de volta só essa fatia (`POST /api/config` só
 * sobrescreve as seções/opções presentes no body — as demais seções do `config.ini` continuam
 * intactas, confirmado em `scripts/servicos.py`/`backend/main.py`).
 */

export interface SecaoConexao {
  server: string;
  port: string;
  database: string;
  username: string;
  password: string;
  driver: string;
}

export interface SecaoEmail {
  servidor_smtp: string;
  porta_smtp: string;
  remetente: string;
  usuario: string;
  senha: string;
  destinatarios: string;
}

export interface SecaoGeral {
  batch_size: number;
  max_workers: number;
  dias_retencao_logs: number;
  dias_retry_attempts: number;
}

export interface SecaoTabelas {
  tabelas_diff: string;
  tabelas_completo: string;
}

/** Forma crua devolvida por `GET /api/config` — vem toda em string (reflexo direto do
 * `configparser`), inclusive os campos numéricos; a conversão pra `number` acontece só no
 * cliente, com `paraNumero`. */
export interface ConfigApiBruta {
  origem?: Partial<SecaoConexao>;
  destino?: Partial<SecaoConexao>;
  email?: Partial<SecaoEmail>;
  configuracao?: Partial<Record<keyof SecaoGeral, string>>;
  tabelas?: Partial<SecaoTabelas>;
}

export function conexaoVazia(): SecaoConexao {
  return { server: '', port: '1433', database: '', username: '', password: '', driver: 'ODBC Driver 17 for SQL Server' };
}

export function emailVazio(): SecaoEmail {
  return { servidor_smtp: '', porta_smtp: '587', remetente: '', usuario: '', senha: '', destinatarios: '' };
}

export function geralVazio(): SecaoGeral {
  return { batch_size: 5000, max_workers: 4, dias_retencao_logs: 14, dias_retry_attempts: 3 };
}

export function tabelasVazias(): SecaoTabelas {
  return { tabelas_diff: '', tabelas_completo: '' };
}

export function paraNumero(valor: string | undefined, padrao: number): number {
  const n = Number(valor);
  return valor && Number.isFinite(n) ? n : padrao;
}

/** Converte o CSV de `tabelas_diff`/`tabelas_completo` numa lista limpa (maiúscula, sem vazios).
 * Também usado por `TabelasService` (core/tabelas.service.ts) pra montar as opções do
 * `po-multiselect` de "tabela específica" — Central de Carga e Auditoria de Dados usam a mesma
 * lista de tabelas configuradas. */
export function paraListaTabelas(csv: string | undefined): string[] {
  return (csv ?? '')
    .split(',')
    .map((nome) => nome.trim().toUpperCase())
    .filter((nome) => nome.length > 0);
}
