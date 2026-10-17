# Manual de Operação — Replicação Dados

**JP Code Craft Solutions** · Edição pública · Revisado em 17/10/2026 para a aplicação Angular + FastAPI deste repositório.

> Esta edição começa sem tabelas ativas. Configure os nomes e modos antes de executar. O servidor escuta em 127.0.0.1 por padrão e não possui autenticação própria.

Guia de configuração, acompanhamento e operação da replicação entre o SQL Server do ERP Protheus (origem) e o banco de BI (destino). Os nomes de telas e ações abaixo correspondem à interface atual.

> A auditoria compara indicadores de integridade. Um resultado “Sincronizada” não comprova igualdade de todos os campos de todos os registros.

## Primeiros passos

1. Acesse **Configurações → Bancos de Dados**, confira origem e destino, use **Testar Conexão** em cada banco e clique em **Salvar Configurações**.
2. Acesse **Configurações → Tabelas**, adicione as tabelas desejadas e indique se participam do DIFF, do COMPLETO ou de ambos. Salve as alterações.
3. Em **Configurações → E-mail**, configure o SMTP e adicione os destinatários. Use **Testar SMTP** e salve.
4. Consulte **Agendador** para conferir se as tarefas estão instaladas, habilitadas e com horários adequados.
5. Use **Auditoria de Dados** para consultar a situação das tabelas. Escolha a carga necessária em **Central de Carga** e acompanhe o resultado no console e no **Histórico de Logs**.

| Menu | Finalidade |
|------|------------|
| Painel Geral | Acompanhar cargas recentes, integridade, frescor dos dados e saúde do servidor |
| Central de Carga | Executar DIFF, SMART COMPLETO, COMPLETO CEGO e validação manual |
| Auditoria de Dados | Comparar indicadores entre ERP e BI e tratar duplicados |
| Agendador | Consultar e controlar tarefas já registradas no Windows |
| Configurações → Bancos de Dados | Editar conexões e parâmetros gerais |
| Configurações → E-mail | Configurar SMTP e destinatários |
| Configurações → Tabelas | Manter o mapeamento de tabelas por modo de carga |
| Histórico de Logs | Consultar logs de DIFF, COMPLETO e validação |
| Documentação | Consultar, buscar e atualizar este manual |

## Painel Geral

A tela inicial reúne os indicadores da operação. Os resultados dependem dos logs e dos dados disponíveis no servidor; um campo sem histórico pode aparecer como “-”.

### Cargas e desempenho

- **Último Status DIFF** e **Último Status Completo**: situação e data das últimas cargas.
- **Registros Inseridos/Atualizados (DIFF)**: total processado e duração da última carga incremental.
- **Tabelas com Erro**: quantidade e nomes das tabelas com erros na última carga DIFF.
- **Taxa de sucesso (últimos 7 dias)**: percentual e quantidade de execuções com sucesso para DIFF e COMPLETO.
- **Volumetria por Tabela**: registros processados na carga incremental recente.
- **Tabelas Mais Lentas**: duração por tabela na carga completa recente.

### Integridade e infraestrutura

- **Última Validação de Integridade (06:00)**: resumo da validação, divergências, correções, duplicados removidos e situação do envio de e-mail. O horário do título é uma referência ao agendamento padrão; confirme a próxima execução no Agendador.
- **Última Auditoria Manual (Aba Auditoria)**: resumo da auditoria consultada na sessão atual do servidor. Esse estado em memória é perdido ao reiniciar o backend.
- **Frescor dos Dados (DIFF)**: tempo decorrido desde a carga incremental, sem garantir que todos os registros estejam atualizados.
- **Próxima Execução Agendada**: informação das tarefas do Windows.
- **Saúde do Servidor**: uso de CPU, memória e disco, quando disponível.
- **Velocidade da Internet do Servidor**: use **Testar agora** para medir a conexão do servidor que executa o backend. O resultado não mede a conexão do computador de quem abriu o navegador nem a velocidade de uma carga SQL.

## Central de Carga

### Escolher o modo de execução

| Modo / botão | Comportamento atual | Uso |
|--------------|---------------------|-----|
| DIFF / Rodar Incremental | Insere e atualiza registros com carimbo mais recente; usa inserção por RECNO quando a tabela não possui STAMP | Sincronização incremental de rotina |
| SMART COMPLETO / Rodar Inteligente | Verifica existência, estrutura, contagem e exclusões lógicas; recarrega as tabelas que exigem correção | Corrigir divergências de volume ou estrutura sem recarregar todas as tabelas |
| COMPLETO CEGO / Forçar Recarga Total | Recarrega integralmente as tabelas da lista COMPLETO, sem a seleção inteligente | Refazer a cópia integral das tabelas mapeadas |
| VALIDADOR / Roda Auditoria | Executa o validador em modo somente análise e tenta enviar o relatório por e-mail | Gerar relatório sem aplicar correções automáticas |

> A carga completa atual prepara uma tabela auxiliar e troca os nomes ao concluir. Ela substitui o conteúdo da tabela de destino pela cópia da origem; não faz mais o truncamento inicial da tabela em uso. Pode consumir tempo, espaço e recursos significativos.

O SMART COMPLETO não compara todos os valores dos registros nem usa diferenças de STAMP como critério de seleção. Alterações que preservam os indicadores podem não provocar recarga inteligente.

Os quatro botões principais usam as listas configuradas para cada rotina. A seleção no campo **Tabelas** só é enviada pelo botão **Disparar Selecionadas**.

### Replicar tabelas específicas

1. No bloco **Replicar Tabelas Específicas (Opcional)**, abra o campo **Tabelas** e selecione os nomes desejados.
2. Escolha **DIFF (Incremental)** ou **COMPLETO** em **Modo de Carga**.
3. Clique em **Disparar Selecionadas**.
4. Acompanhe o **Console de Saída em Tempo Real** e consulte o log para verificar avisos e erros.

É necessário selecionar pelo menos uma tabela para usar **Disparar Selecionadas**. Nesse bloco, o modo COMPLETO faz recarga integral das tabelas escolhidas; não ativa a opção SMART.

### Acompanhar a execução

Os botões de disparo ficam desabilitados enquanto há uma execução manual ativa. O console mostra a saída acumulada e, quando disponível, o progresso por tabela. O indicador **Ocioso** significa que o processo terminou ou não está rodando; confira o log para saber se houve sucesso.

O replicador utiliza uma trava no banco de destino para evitar duas replicações simultâneas, inclusive entre disparos manuais e agendados. Se o log indicar execução bloqueada, confira as tarefas em andamento antes de tentar novamente.

## Auditoria de Dados

A auditoria da tela consulta indicadores por tabela e mantém os resultados na memória do servidor. Ela não envia relatório por e-mail e não aplica correções automaticamente ao iniciar a consulta.

### Executar e interpretar

1. Clique em **Auditar Todas** para consultar a lista configurada em COMPLETO.
2. Para reduzir o escopo, selecione tabelas no campo próprio e clique em **Auditar Selecionadas**.
3. Acompanhe a barra de progresso e use **Filtrar Resultados** para localizar uma tabela pelo nome.
4. Consulte o status e as ações disponíveis no menu da linha.

Na seleção parcial, o backend cruza os nomes com a lista COMPLETO. Se houver correspondências, são essas as tabelas auditadas; se não houver nenhuma, usa os nomes informados. Para conferir uma tabela exclusiva do DIFF, selecione-a isoladamente.

O botão **Cancelar** solicita a interrupção do laço da auditoria. Uma consulta SQL que já está em andamento pode terminar antes que a interrupção seja percebida.

| Coluna | Significado |
|--------|-------------|
| Tabela | Nome físico da tabela |
| Reg. Prod / Reg. BI | Quantidade física de registros na origem e no destino |
| Del. Prod / Del. BI | Quantidade de registros com exclusão lógica |
| U.A. Prod / U.A. BI | Última atualização disponível em cada banco, exibida para consulta |
| Duplic. BI | Quantidade de ocorrências excedentes do mesmo R_E_C_N_O_ no destino |
| Status | Resultado da comparação ou erro da consulta |

### O que cada status significa

- **Sincronizada (OK)**: as contagens de registros e de exclusões lógicas coincidem e não foram detectados duplicados no BI.
- **Divergente**: a contagem de registros ou de exclusões lógicas difere. Use **Ver divergência** para consultar o detalhe.
- **Duplicados**: as contagens coincidem, mas existem ocorrências repetidas do mesmo RECNO no BI. Também pode haver duplicados em uma linha com status Divergente; confira a coluna própria.
- **Inexistente no ERP**, **Inexistente no BI** ou **Sem tabela**: a tabela está ausente em um ou nos dois bancos.
- **ERRO**: ocorreu falha ao consultar aquela tabela. Examine a mensagem exibida na linha e os dados de conexão.

**As datas U.A. Prod e U.A. BI não determinam o status da auditoria atual.** Uma diferença de STAMP com contagens iguais pode continuar aparecendo como Sincronizada. Também não há comparação campo a campo ou validação de estrutura nesta tela. O contador **Erro/Inexistente** atual soma os status de tabela inexistente; erros SQL devem ser conferidos nas linhas e na mensagem geral.

### Replicar e remover duplicados

A ação **Replicar**, disponível para divergência ou tabela ausente no BI, dispara uma carga **DIFF** da tabela. Acompanhe-a na **Central de Carga** e execute nova auditoria ao terminar. O DIFF pode não resolver registros extras ou alterações de estrutura; nesses casos avalie a carga completa.

**Remover duplicados** exclui permanentemente as ocorrências excedentes de RECNO no BI, mantendo uma ocorrência por chave. Não altera a origem. Há ação por linha e o botão **Remover Duplicados de Todas as Tabelas**, que usa os resultados da última auditoria, inclusive linhas ocultas pelo filtro de nome.

> A remoção altera dados do destino. A ação em lote solicita confirmação; a ação individual atual executa diretamente. Verifique a tabela antes de acioná-la. Os registros removidos são gravados em logs/duplicados, pasta que não aparece na tela Histórico de Logs.

Após a remoção, execute nova auditoria: os resultados anteriores não são recalculados apenas por atualizar a tela.

## Agendador

Esta página controla tarefas existentes no **Agendador de Tarefas do Windows do servidor**. Não cria tarefas e não oferece edição de horários pela interface.

| Tarefa | Configuração padrão do instalador |
|--------|----------------------------------|
| Protheus BI - Replicacao DIFF | A cada 5 minutos |
| Protheus BI - Replicacao COMPLETO | Diariamente às 02:00, com SMART |
| Protheus BI - Validacao Integridade | Diariamente às 06:00, com correção automática habilitada por padrão |

Os horários podem ter sido alterados no Windows. Confira **Status**, **Última Execução**, **Próxima Execução** e **Resultado** na tabela. Resultado zero indica conclusão sem erro reportado pela tarefa; confira os logs para avaliar o processamento.

### Ações no menu da linha

- **Ativar**: habilita uma tarefa desativada.
- **Pausar**: desabilita futuros disparos automáticos; não encerra uma execução que já começou.
- **Rodar Agora**: solicita execução imediata de uma tarefa que não esteja executando.
- **Parar**: solicita interrupção forçada de uma tarefa em execução.
- **Tarefa não instalada**: informa que a tarefa ainda precisa ser registrada no Windows.

Ao monitorar uma tarefa conhecida em execução, a página apresenta log ao vivo e progresso quando disponível. Se várias estiverem rodando, um log é exibido por vez. Para obter uma nova consulta quando não há tarefa ativa, saia da página e entre novamente; não existe botão **Atualizar Status** nesta versão.

### Instalação e permissões

O administrador pode registrar as tarefas com **docs/instalar_tarefas.ps1**, executado em PowerShell com as permissões necessárias. O instalador pode substituir tarefas de mesmo nome. Confira usuário, caminhos, credenciais e horários antes de executá-lo.

Em caso de erro, consulte o histórico da tarefa no Windows e os logs da rotina. Não conclua a causa apenas pelo código numérico: valide a conta de execução, acesso ao Python e permissões de banco e de pasta.

## Configurações de bancos de dados

Em **Configurações → Bancos de Dados**, os dois blocos separam a **origem (ERP Protheus)** do **destino (BI)**.

Preencha servidor, porta, nome do banco, usuário SQL Server, senha e driver ODBC instalado no servidor. Use **Testar Conexão** em cada bloco. O teste usa os valores do formulário e não salva as alterações; confirme depois em **Salvar Configurações**.

Senhas já salvas são mascaradas. Quando o campo está vazio ou contém a máscara no teste de conexão, o backend tenta usar a credencial existente ou a variável de ambiente correspondente. Não inclua senhas em capturas de tela ou em logs compartilhados.

### Performance e manutenção

| Campo | Função | Valor inicial da interface |
|-------|--------|----------------------------|
| Tamanho do Lote (batch_size) | Quantidade de registros lidos por lote | 5000 |
| Workers Paralelos (Threads) | Concorrência de processamento de tabelas | 4 |
| Retenção de Logs (Dias) | Período usado pela limpeza de logs | 14 |
| Tentativas de Conexão (Retry) | Limite de tentativas configurado | 3 |

Os valores exibidos vêm da configuração salva; a tabela acima indica os valores iniciais usados pela interface quando não há valor disponível. Mais workers não garantem maior velocidade: considere os recursos dos bancos e da rede.

As alterações são persistidas em **config/config.ini**. Cada página de configuração salva sua própria seção; editar bancos não exige reenviar a lista de destinatários ou de tabelas.

## Configurações de e-mail

Em **Configurações → E-mail**, preencha servidor SMTP, porta, remetente, usuário e senha ou senha de aplicativo.

1. Informe o endereço em **E-mail** no bloco de destinatários.
2. Clique em **Adicionar** para incluí-lo na lista; repita para os demais endereços.
3. Use a ação **Remover** na linha para retirar um destinatário.
4. Clique em **Salvar Configurações** para persistir SMTP e lista de destinatários.

**Testar SMTP** verifica conexão, STARTTLS e autenticação com o servidor informado. **Não envia mensagem de teste**, não valida a entrega ao destinatário e não salva o formulário. O teste atual utiliza STARTTLS; uma configuração que exija TLS implícito pode não funcionar nesse teste.

Para avaliar o envio real, salve a configuração, execute **VALIDADOR → Roda Auditoria** na Central de Carga e confira o log de validação e o resultado do envio no Painel Geral. A auditoria iniciada na tela **Auditoria de Dados** não faz esse envio.

## Configurações de tabelas

Em **Configurações → Tabelas**:

1. Informe o nome físico, por exemplo **SE4010**.
2. Marque **DIFF**, **COMPLETO** ou ambos.
3. Clique em **Adicionar**. Se a tabela já estiver listada, os modos marcados são incorporados à linha existente.
4. Use **Tirar do DIFF** ou **Tirar do COMPLETO** nas ações da linha para retirar a participação naquele modo.
5. Clique em **Salvar Configurações**.

Quando nenhum modo permanece ativo, a linha é retirada da lista. Remover o mapeamento não apaga a tabela no BI, mas retira sua participação nas próximas execuções daquele modo.

As listas alimentam os seletores de tabelas da Central de Carga e da Auditoria. **Auditar Todas** e o validador usam a lista COMPLETO. Reabra essas telas após salvar para carregar as opções atualizadas.

## Histórico de Logs

Há três grupos: **DIFF**, **COMPLETO** e **Validação**. Cada lista apresenta os arquivos recentes disponíveis, com nome, data de modificação e tamanho. A listagem tem um limite por modo e não representa necessariamente todo o arquivo histórico do servidor.

Use a ação **Visualizar** no menu da linha para abrir o conteúdo no console da página. **Fechar** oculta o visualizador. A leitura de um arquivo histórico não acompanha novas linhas automaticamente; para execução ao vivo, consulte Central de Carga ou Agendador.

Os logs detalham tabelas, registros processados, duração, avisos e erros. Os arquivos de remoção de duplicados ficam separados em **logs/duplicados** e precisam ser consultados no servidor.

## Conceitos de replicação e integridade

### DIFF, STAMP e RECNO

Quando existe a coluna **S_T_A_M_P_**, o DIFF usa o maior carimbo do destino como ponto de corte e processa os registros mais recentes da origem. Faz inserção ou atualização usando **R_E_C_N_O_** como chave.

Sem STAMP, o fallback usa **R_E_C_N_O_** para inserir registros novos com chave acima do maior valor presente no BI. Nesse caso, não detecta alterações em registros já existentes.

O DIFF não elimina registros fisicamente removidos da origem. Registros extras no destino podem exigir reconciliação pelo validador com correção ou uma recarga completa. Não use uma execução DIFF como garantia de reparação de qualquer divergência histórica.

### Exclusão lógica

O Protheus utiliza **D_E_L_E_T_** para marcar registros excluídos logicamente, geralmente com asterisco. As contagens físicas ainda incluem essas linhas; a auditoria compara a quantidade de exclusões separadamente.

### Auditoria manual, validador e SMART

| Recurso | Consulta | Correção automática | E-mail |
|---------|----------|---------------------|--------|
| Auditoria de Dados | Registros, exclusões lógicas, últimas atualizações e duplicados | Não; ações de correção são disparadas separadamente | Não |
| VALIDADOR na Central de Carga | Validação de integridade em somente análise | Não | Tenta enviar relatório |
| Tarefa de Validação de Integridade | Validação com correção habilitada por padrão | Pode remover duplicados, reconciliar exclusões e recarregar divergentes | Tenta enviar relatório |
| SMART COMPLETO | Existência, estrutura, registros e exclusões lógicas | Recarrega tabelas selecionadas pela verificação | Não é o fluxo de envio do validador |

Contagens iguais não garantem conteúdo idêntico. Alterações no ERP durante uma consulta ou carga também podem produzir diferenças temporárias. Confirme o resultado após a conclusão da carga e investigue divergências persistentes.

## Iniciar e acessar a aplicação

### Visualizar a versão compilada

No PowerShell, a partir da pasta raiz do projeto:

```powershell
.\.venv\Scripts\python.exe .\backend\main.py
```

Abra **http://localhost:5050** e mantenha o terminal aberto. Use **Ctrl+C** nesse terminal para encerrar o processo iniciado por ele.

A porta vem de **[configuracao] port** em config/config.ini; quando não preenchida, o backend usa PORT do ambiente ou 5050. Para acesso pela rede, utilize o endereço do servidor e a porta configurada, com o acesso de rede liberado pelo responsável pelo ambiente.

O backend serve a interface compilada em **frontend/dist/frontend/browser**. Após alterações no frontend, gere o build dentro da pasta frontend:

```powershell
npm.cmd run build
```

Se o backend tiver sido iniciado sem um build existente, reinicie-o após compilar para registrar as rotas da interface.

### Desenvolvimento com atualização automática

Mantenha o backend rodando e abra outro PowerShell na pasta frontend:

```powershell
npm.cmd start
```

Abra **http://localhost:4200**. O frontend de desenvolvimento consulta a API em **http://localhost:5050**; se a porta do backend mudar, ajuste também o endereço de desenvolvimento.

### Encerrar e acessar pela rede

Use **Ctrl+C** no terminal que iniciou o backend. Os atalhos legados de iniciar e parar não integram esta distribuição. A variável de ambiente **HOST** controla o endereço de escuta; somente habilite acesso de rede depois de aplicar autenticação e proteção externa, conforme README.md e SECURITY.md.

## Resolver problemas e manter o manual

### Identificar a versão da aplicação

A versão da interface aparece no rodapé de todas as telas, ao lado do nome da aplicação. Informe esse número ao relatar problemas para identificar qual versão está aberta no navegador. Após uma atualização, use **Ctrl+F5** para carregar a interface mais recente.

### O painel não abre ou fica carregando

Confira a mensagem do terminal do backend, o endereço e a porta. No ambiente de desenvolvimento, verifique se backend e frontend estão rodando. Se a interface carregar e as consultas falharem, verifique a API e as conexões de banco.

### A tabela continua divergente após Replicar

A ação **Replicar** usa DIFF. Confira se há registros extras, ausência de STAMP ou diferença de estrutura. Avalie SMART COMPLETO ou COMPLETO para o escopo necessário, acompanhe o log e repita a auditoria ao finalizar.

### O relatório não chega por e-mail

Salve SMTP e destinatários, confira o teste de autenticação e execute o validador manual. Consulte o log e a situação de envio no Painel Geral. Testar SMTP com sucesso não comprova entrega de mensagem; confira também rejeições e filtros do serviço de e-mail.

### A tarefa aparece como Não Instalada

O painel não registra tarefas automaticamente. Solicite ao administrador a instalação no Windows e confira os caminhos para a cópia correta do projeto e do Python.

### A carga foi bloqueada ou não iniciou

Confira o console, a Central de Carga e o Agendador. Outra replicação pode estar com a trava do banco. Aguarde sua conclusão e examine os logs antes de repetir o disparo.

### Atualizar este manual

O conteúdo vem de **docs/DOCUMENTACAO.md**, consultado por **GET /api/docs**. Edite o arquivo em UTF-8 e use **Atualizar manual**, ou reabra a página. Não é necessário recompilar o frontend para alterar apenas o texto.

Use **Buscar no manual** para filtrar assuntos pelo conteúdo, com ou sem acentos, e o índice **Neste manual** para ir à seção desejada. **Limpar busca** restaura a leitura completa.

