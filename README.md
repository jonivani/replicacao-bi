# Replicação Dados — Protheus & BI

Aplicação da **JP Code Craft Solutions** para replicar tabelas do ERP Protheus entre dois bancos SQL Server, acompanhar execuções e consultar indicadores de integridade.

**Versão:** 2.0.0 · **Licença:** MIT · **Interface:** português do Brasil

## Recursos

- Painel com cargas recentes, taxa de sucesso, frescor dos dados e saúde do servidor.
- Carga incremental (DIFF), carga completa e seleção inteligente (SMART COMPLETO).
- Execução manual de tabelas específicas com console ao vivo.
- Auditoria de contagens, exclusões lógicas e duplicados.
- Validador com relatório por e-mail e opção de correção automática nos jobs.
- Configuração de bancos, SMTP, destinatários e mapeamento de tabelas.
- Integração com o Agendador de Tarefas do Windows.
- Histórico de logs e manual pesquisável dentro da aplicação.

## Antes de instalar

Esta edição disponibiliza o código da aplicação. Não inclui acesso ao Protheus, bancos, dados de demonstração nem credenciais.

O painel **não possui autenticação própria** e permite alterar configurações e executar operações de banco. O servidor desta edição escuta em **127.0.0.1 por padrão**. Para acesso compartilhado, use autenticação e controle de acesso externos; não exponha a API diretamente à internet. Publicar o código no GitHub é diferente de hospedar o painel. GitHub Pages não executa este backend Python.

As listas de tabelas começam vazias e não há mapeamentos específicos de clientes no código público. Configure explicitamente as tabelas de seu ambiente antes de executar cargas. Teste inicialmente com bancos de homologação.

## Requisitos

- Windows para os recursos de agendamento e os comandos de instalação abaixo.
- Python 3.10 ou superior, com suporte a ambientes virtuais.
- Node.js 24 e npm para compilar o frontend Angular 21.
- Microsoft ODBC Driver 17 for SQL Server instalado e compatível com o Python utilizado.
- Dois bancos SQL Server acessíveis: origem e destino. O destino deve permitir as operações de criação, escrita e troca de tabelas utilizadas pelo replicador.
- Acesso à rede dos bancos; SMTP é necessário para envio de relatórios.

Outros sistemas operacionais não foram validados para a aplicação completa. O Agendador depende das ferramentas do Windows.

## Instalação local

Abra o PowerShell **na raiz deste repositório**, onde ficam as pastas backend, config e frontend.

### 1. Preparar o Python

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\config\requirements.txt
```

### 2. Criar a configuração local

```powershell
Copy-Item .\config\config.example.ini .\config\config.ini
```

Preencha conexões e senhas no arquivo local. Ele está excluído do Git. Também é possível configurar as conexões pela interface após iniciar o painel.

As variáveis de ambiente têm prioridade sobre o INI:

| Origem | Destino | SMTP |
|--------|---------|------|
| DB_ORIGEM_SERVER | DB_DESTINO_SERVER | SMTP_SERVER |
| DB_ORIGEM_PORT | DB_DESTINO_PORT | SMTP_PORT |
| DB_ORIGEM_DB | DB_DESTINO_DB | SMTP_USER |
| DB_ORIGEM_USER | DB_DESTINO_USER | SMTP_PASS |
| DB_ORIGEM_PASS | DB_DESTINO_PASS | SMTP_FROM |
| DB_ORIGEM_DRIVER | DB_DESTINO_DRIVER | SMTP_TO |

O projeto não carrega arquivos `.env` automaticamente; configure as variáveis no processo ou no sistema operacional.

### 3. Compilar a interface

```powershell
cd frontend
npm.cmd ci
npm.cmd run build
cd ..
```

### 4. Iniciar

```powershell
.\.venv\Scripts\python.exe .\backend\main.py
```

Abra **http://localhost:5050**. O backend serve a interface compilada e a API no mesmo endereço. Mantenha o terminal aberto; **Ctrl+C** encerra essa execução.

A porta é definida por `[configuracao] port` no INI. Se não estiver preenchida, usa `PORT` do ambiente ou 5050. A variável `HOST` pode alterar o endereço de escuta; mantenha o padrão local enquanto não houver proteção externa.

### 5. Configurar a operação

1. Em **Configurações → Bancos de Dados**, confira origem e destino e teste as conexões.
2. Em **Configurações → Tabelas**, adicione os nomes físicos e os modos DIFF/COMPLETO e salve.
3. Se utilizar relatórios, configure SMTP e destinatários e salve.
4. Consulte **Documentação** para os modos de carga, limites da auditoria e tratamento de divergências.
5. Execute uma auditoria inicial antes de iniciar cargas e confira os logs após cada execução.

O histórico estruturado é opcional. Para utilizá-lo, execute **db/historico-execucoes.sql** no banco BI de destino selecionado. O replicador registra falhas desse histórico sem interromper a carga principal.

## Desenvolvimento

Inicie o backend como acima. Em outro terminal, dentro de frontend:

```powershell
npm.cmd start
```

Acesse **http://localhost:4200**. A API de desenvolvimento está configurada para **http://localhost:5050** em `frontend/src/environments/environment.ts`.

Para verificar o frontend, execute dentro de frontend:

```powershell
npm.cmd test -- --watch=false
npm.cmd run build
```

## Agendamentos do Windows

O administrador pode instalar as tarefas a partir da raiz do projeto:

```powershell
.\docs\instalar_tarefas.ps1 -Tarefa todas
```

O script pode substituir tarefas existentes de mesmo nome. Revise o usuário, os caminhos e os horários antes de executá-lo. Os padrões são DIFF a cada 5 minutos, SMART COMPLETO às 02:00 e validação com correção automática às 06:00. Para consultar sem corrigir, execute o validador com `--somente-analise`, ou use a ação VALIDADOR na Central de Carga.

## Limites importantes

- Auditoria com contagens iguais não garante igualdade de todos os campos; STAMP é exibido, mas não determina o status da auditoria manual.
- Sem STAMP, o DIFF insere novos RECNOs e não detecta alterações de registros existentes.
- O DIFF não elimina registros fisicamente removidos da origem.
- Carga completa substitui a tabela do destino por uma cópia preparada em tabela auxiliar; confirme o escopo antes de executar.
- Remoção de duplicados altera permanentemente o destino. Logs podem conter conteúdo dos registros removidos.
- O teste SMTP autentica a conexão, mas não envia mensagem de teste.
- O teste de velocidade usa o serviço público speed.cloudflare.com e também pode ser executado após uma carga DIFF. Não transmite registros SQL, mas gera tráfego externo a partir do servidor.

## Estrutura

```text
backend/        API FastAPI e entrega do frontend compilado
config/         Configuração de exemplo e dependências Python
frontend/       Interface Angular + PO UI, testes e versão
scripts/        Replicação, auditoria, histórico, SMTP e monitoramento
db/             Estrutura opcional do histórico de execuções
docs/           Manual e instalador de tarefas do Windows
```

## Publicar em um repositório próprio

Esta pasta foi preparada sem histórico Git do ambiente original. Crie um **repositório vazio** no GitHub, sem README ou licença adicionais, e abra um terminal nesta pasta:

```powershell
git init -b main
git add .
git diff --cached --stat
git commit -m "Publica versão inicial 2.0.0"
```

Depois adicione o endereço do repositório criado e envie a branch main. Revise os arquivos preparados antes do envio. Não envie a pasta do ambiente original nem arquivos de configuração, logs ou backups locais.

## Licença e créditos

Código sob a licença [MIT](LICENSE). Desenvolvido por [JP Code Craft Solutions](https://jpcodecraft.com.br/).

Protheus e TOTVS pertencem aos respectivos titulares. As dependências mantêm suas próprias licenças. A licença do código não concede direitos sobre marcas de terceiros nem implica afiliação oficial. Consulte [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) e [SECURITY.md](SECURITY.md).
