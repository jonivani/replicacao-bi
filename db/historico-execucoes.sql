-- Histórico opcional de execuções. Execute no banco BI de destino selecionado.
-- Não contém dados de operação; cria somente tabelas e índices.

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'REPLICACAO_EXECUCOES')
BEGIN
    CREATE TABLE REPLICACAO_EXECUCOES (
        ID                   INT IDENTITY(1,1) PRIMARY KEY,
        MODO                 VARCHAR(20)   NOT NULL,   -- diff | completo | validacao
        SMART                BIT           NOT NULL DEFAULT 0,  -- só relevante quando MODO = completo
        TABELA_ALVO          VARCHAR(400)  NULL,        -- preenchido só quando é carga de tabela(s) específica(s)
        STATUS               VARCHAR(20)   NOT NULL,    -- RODANDO | SUCESSO | FALHA
        DATA_INICIO          DATETIME2     NOT NULL,
        DATA_FIM             DATETIME2     NULL,
        DURACAO_SEG          DECIMAL(10,2) NULL,
        TOTAL_TABELAS        INT           NULL,
        TABELAS_ATUALIZADAS  INT           NULL,
        REGISTROS_INSERIDOS  BIGINT        NULL,
        ERRO                 VARCHAR(MAX)  NULL
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_REPLICACAO_EXECUCOES_MODO_DATA')
BEGIN
    CREATE INDEX IX_REPLICACAO_EXECUCOES_MODO_DATA ON REPLICACAO_EXECUCOES (MODO, DATA_INICIO DESC);
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'REPLICACAO_EXECUCOES_TABELAS')
BEGIN
    CREATE TABLE REPLICACAO_EXECUCOES_TABELAS (
        ID           INT IDENTITY(1,1) PRIMARY KEY,
        EXECUCAO_ID  INT           NOT NULL,
        TABELA       VARCHAR(50)   NOT NULL,
        REGISTROS    BIGINT        NULL,
        TEMPO_SEG    DECIMAL(10,2) NULL,  -- reservado para tempo por tabela
        STATUS       VARCHAR(20)   NOT NULL,   -- SUCESSO | SEM_ALTERACAO | ERRO | conforme STATUS_TABELA do validador
        CONSTRAINT FK_REPLICACAO_EXECUCOES_TABELAS_EXECUCAO
            FOREIGN KEY (EXECUCAO_ID) REFERENCES REPLICACAO_EXECUCOES(ID)
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_REPLICACAO_EXECUCOES_TABELAS_EXEC')
BEGIN
    CREATE INDEX IX_REPLICACAO_EXECUCOES_TABELAS_EXEC ON REPLICACAO_EXECUCOES_TABELAS (EXECUCAO_ID);
END;
GO

