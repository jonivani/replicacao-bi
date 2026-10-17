/** Formatação do manual interno. HTML de origem é sempre escapado;
 * a saída ainda passa pelo sanitizador padrão do Angular em [innerHTML]. */
export function markdownParaHtml(md: string): string {
  const linhas = md.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').split(/\r?\n/);
  let html = '';
  let lista: 'ul' | 'ol' | null = null;
  let codigo = false;
  const fecharLista = () => {
    if (lista) html += `</${lista}>\n`;
    lista = null;
  };
  const celulas = (linha: string) => linha.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
  const separador = (linha: string) => /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(linha.trim());

  for (let i = 0; i < linhas.length; i++) {
    const linha = linhas[i].trimEnd();
    const aparada = linha.trim();
    if (aparada.startsWith('```')) {
      fecharLista();
      html += codigo ? '</code></pre>\n' : '<pre><code>';
      codigo = !codigo;
      continue;
    }
    if (codigo) {
      html += linha + '\n';
      continue;
    }
    if (!aparada) {
      fecharLista();
      continue;
    }
    if (aparada.includes('|') && i + 1 < linhas.length && separador(linhas[i + 1])) {
      fecharLista();
      const cabecalho = celulas(aparada);
      html += '<div class="doc-tabela"><table><thead><tr>';
      html += cabecalho.map(c => `<th scope="col">${paraHtmlInline(c)}</th>`).join('');
      html += '</tr></thead><tbody>';
      i += 2;
      while (i < linhas.length && linhas[i].trim().includes('|') && linhas[i].trim()) {
        const valores = celulas(linhas[i]);
        html += '<tr>' + cabecalho.map((_, j) => `<td>${paraHtmlInline(valores[j] ?? '')}</td>`).join('') + '</tr>';
        i++;
      }
      i--;
      html += '</tbody></table></div>\n';
      continue;
    }
    const titulo = /^(#{1,4}) (.+)$/.exec(aparada);
    if (titulo) {
      fecharLista();
      const nivel = titulo[1].length;
      html += `<h${nivel}>${paraHtmlInline(titulo[2])}</h${nivel}>\n`;
      continue;
    }
    if (aparada === '---') {
      fecharLista();
      html += '<hr>\n';
      continue;
    }
    const item = /^(?:[-*] |\d+\. )(.+)$/.exec(aparada);
    if (item) {
      const tipo = /^\d+\./.test(aparada) ? 'ol' : 'ul';
      if (lista !== tipo) {
        fecharLista();
        lista = tipo;
        html += `<${tipo}>\n`;
      }
      html += `<li>${paraHtmlInline(item[1])}</li>\n`;
      continue;
    }
    fecharLista();
    if (aparada.startsWith('&gt;')) {
      html += `<blockquote><p>${paraHtmlInline(aparada.replace(/^&gt;\s?/, ''))}</p></blockquote>\n`;
    } else {
      html += `<p>${paraHtmlInline(linha)}</p>\n`;
    }
  }
  fecharLista();
  if (codigo) html += '</code></pre>\n';
  return html;
}

function paraHtmlInline(texto: string): string {
  return texto.split(/(`[^`]+`)/g).map(trecho =>
    trecho.startsWith('`') && trecho.endsWith('`')
      ? `<code>${trecho.slice(1, -1)}</code>`
      : trecho.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'))
    .join('');
}

export interface SecaoDocumentacao {
  id: string;
  titulo: string;
  markdown: string;
  html: string;
}

/** Divide apenas títulos fora de blocos de código, mantendo IDs estáveis na busca. */
export function dividirDocumentacao(markdown: string): { introducao: string; secoes: SecaoDocumentacao[] } {
  const introducao: string[] = [];
  const secoes: SecaoDocumentacao[] = [];
  let codigo = false;
  for (const linha of markdown.split(/\r?\n/)) {
    if (linha.trim().startsWith('```')) codigo = !codigo;
    const titulo = !codigo && /^## (.+)$/.exec(linha);
    if (titulo) {
      secoes.push({ id: `doc-secao-${secoes.length + 1}`, titulo: titulo[1], markdown: '', html: '' });
    }
    if (secoes.length) secoes[secoes.length - 1].markdown += linha + '\n';
    else introducao.push(linha);
  }
  for (const secao of secoes) secao.html = markdownParaHtml(secao.markdown);
  return { introducao: markdownParaHtml(introducao.join('\n')), secoes };
}
