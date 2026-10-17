# Contribuindo

1. Leia README.md e configure um ambiente de homologação com dados autorizados para teste.
2. Crie uma branch para a alteração.
3. Execute os testes e o build na pasta frontend.
4. Atualize docs/DOCUMENTACAO.md se o comportamento de alguma tela mudar.
5. Envie um pull request explicando a mudança e como foi validada.

Não inclua config.ini, logs reais, senhas, dumps, mapeamentos de clientes ou dependências instaladas. Para atualizar a versão, use npm version na pasta frontend com --no-git-tag-version e mantenha package-lock.json sincronizado.

O envio de contribuições ocorre sob a licença MIT do projeto. A automação completa do backend contra SQL Server exige um ambiente próprio; não aponte testes para produção.
