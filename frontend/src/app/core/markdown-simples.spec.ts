import { dividirDocumentacao, markdownParaHtml } from './markdown-simples';

describe('markdown do manual', () => {
  it('renderiza tabelas com cabeçalhos acessíveis e mantém o texto seguinte', () => {
    const html = markdownParaHtml('| Campo | Valor |\n|---|---|\n| **Modo** | `DIFF` |\n\nDepois');
    expect(html).toContain('<th scope="col">Campo</th>');
    expect(html).toContain('<td><strong>Modo</strong></td><td><code>DIFF</code></td>');
    expect(html).toContain('</table></div>\n<p>Depois</p>');
  });

  it('renderiza instruções numeradas, listas e avisos', () => {
    const html = markdownParaHtml('1. Abrir\n2. Salvar\n\n- Consultar\n\n> **Atenção**');
    expect(html).toContain('<ol>\n<li>Abrir</li>\n<li>Salvar</li>\n</ol>');
    expect(html).toContain('<ul>\n<li>Consultar</li>\n</ul>');
    expect(html).toContain('<blockquote><p><strong>Atenção</strong></p></blockquote>');
  });

  it('escapa HTML e preserva exemplos de código sem interpretar sua formatação', () => {
    const html = markdownParaHtml('<img src=x onerror=alert(1)>\n\n`**literal**`\n\n```html\n<script>alert(1)</script>\n## Não é título\n```');
    expect(html).not.toContain('<img');
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
    expect(html).toContain('<code>**literal**</code>');
    expect(html).toContain('## Não é título');
    expect(html).not.toContain('<h2>');
  });

  it('fecha blocos de código incompletos', () => {
    expect(markdownParaHtml('```\nexemplo')).toBe('<pre><code>exemplo\n</code></pre>\n');
  });

  it('divide assuntos preservando introdução, IDs e títulos dentro do código', () => {
    const resultado = dividirDocumentacao('# Manual\n\n## Configurar\nTexto\n```\n## Exemplo\n```\n## Operar\nPassos');
    expect(resultado.introducao).toContain('<h1>Manual</h1>');
    expect(resultado.secoes.map(s => s.titulo)).toEqual(['Configurar', 'Operar']);
    expect(resultado.secoes.map(s => s.id)).toEqual(['doc-secao-1', 'doc-secao-2']);
    expect(resultado.secoes[0].html).toContain('## Exemplo');
  });
});
