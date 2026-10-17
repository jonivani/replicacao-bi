# Segurança

## Operação do painel

O painel não implementa login, papéis de acesso, proteção CSRF ou autenticação da API. CORS não substitui autenticação. Esta edição escuta somente em 127.0.0.1 por padrão; manter o código público não torna o serviço apropriado para exposição pública direta.

Antes de disponibilizar o painel para outros usuários, aplique autenticação, HTTPS e restrição de rede em uma camada externa. Use contas SQL com permissões adequadas e uma origem somente leitura sempre que possível. Revise cargas e exclusões em homologação.

As conexões SQL atuais usam Encrypt=yes com TrustServerCertificate=yes. Isso permite criptografia sem verificar a identidade do servidor pelo certificado. Avalie a validação de certificados para o ambiente de destino antes de produção.

## Informações locais

Não publique config/config.ini, variáveis de ambiente, senhas, endereços internos, dumps, logs ou backups. O .gitignore reduz inclusões acidentais no Git, mas não protege contra upload manual ou adição forçada.

Os logs de remoção de duplicados podem conter dados dos registros excluídos. Ao abrir um relato público, substitua nomes de clientes, endereços, usuários e conteúdo dos registros por exemplos fictícios.

## Relatar problemas

Para falhas que não exponham informações confidenciais, abra uma issue com versão, passos e mensagens sanitizadas. Para vulnerabilidades, utilize a opção de relato privado do GitHub caso esteja habilitada no repositório, ou procure o mantenedor por um canal privado já estabelecido. Não publique credenciais ou detalhes de exploração em issues públicas.
