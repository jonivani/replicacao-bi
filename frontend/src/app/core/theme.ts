import { PoTheme } from '@po-ui/ng-components';

/** Identidade JP Code Craft: verde profundo, sálvia e marfim.
 * Paleta institucional: https://jpcodecraft.com.br/assets/css/style.css (V2).
 * Mantém as cores semânticas de feedback e o nível AA do PO UI.
 */
export const TEMA_REPLICACAO_BI: PoTheme = {
  name: 'replicacao-bi',
  type: {
    light: {
      color: {
        brand: {
          '01': {
            lightest: '#EDF2EA',
            lighter: '#D5DFD0',
            light: '#8EA58D',
            base: '#285B45',
            dark: '#183B32',
            darker: '#123027',
            darkest: '#0B211B',
          },
          '02': {
            lightest: '#F7F7F2',
            lighter: '#EEEFE8',
            light: '#9BA99B',
            base: '#526059',
            dark: '#3D4B43',
            darker: '#28382F',
            darkest: '#183B32',
          },
          '03': {
            lightest: '#F6EDE2',
            lighter: '#EBD5BC',
            light: '#D6AA77',
            base: '#B87535',
            dark: '#925A28',
            darker: '#6D421C',
            darkest: '#482C13',
          },
        },
        action: {
          default: 'var(--color-brand-01-base)',
          hover: 'var(--color-brand-01-dark)',
          pressed: 'var(--color-brand-01-darker)',
          disabled: 'var(--color-neutral-light-30)',
          focus: 'var(--color-brand-01-darkest)',
        },
        // Neutros padrão do PO-UI, repetidos aqui (não é uma escolha de marca) porque
        // `PoThemeService.getSelectBgIconsStyle()` acessa `themeColor.neutral.dark[70]` sem optional
        // chaining assim que `brand['01'].dark` existe — omitir `neutral` no tema custom derruba o
        // app inteiro com `TypeError: Cannot read properties of undefined (reading 'dark')` no
        // `setTheme()` do bootstrap. Achado testando no navegador (Playwright) — `ng build` não pega,
        // é um erro em tempo de execução dentro da própria lib.
        neutral: {
          light: { '00': '#ffffff', '05': '#fbfbfb', '10': '#eceeee', '20': '#dadedf', '30': '#b6bdbf' },
          mid: { '40': '#9da7a9', '60': '#6e7c7f' },
          dark: { '70': '#4a5c60', '80': '#2c3739', '90': '#1d2426', '95': '#0b0e0e' },
        },
      },
      // Suaviza o scrim do `po-loading-overlay`/`po-overlay` (decisão do usuário em 22/08/2026:
      // "mais suave pra usabilidade"). O `::before` do `.po-overlay` (usado no modo contido, SEM
      // `p-screen-lock` — é o que as telas do app usam) pinta com `--color-overlay` (por padrão
      // `var(--color-neutral-dark-80)`, um cinza bem escuro `#2c3739`) numa opacidade fixa de
      // `--opacity-overlay: 0.7` — daí o bloco cinza carregado visualmente "pesado". Achado
      // inspecionando o `::before` ao vivo (`getComputedStyle(el, '::before')` via Playwright,
      // não aparece em nenhuma ferramenta estática) porque `--color-overlay-background-color-
      // overlay` (a variável óbvia pelo nome) só é usada pelo modo `p-screen-lock=true`
      // (`.po-overlay-fixed`), que este app não usa em lugar nenhum.
      onRoot: {
        '--color-overlay': '#ffffff',
        '--opacity-overlay': '0.6',
      },
    },
  },
};
