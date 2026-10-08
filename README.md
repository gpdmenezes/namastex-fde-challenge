
# AutoSeguro — Agente de Cotação de Seguros com IA

Agente conversacional de cotação de seguros automotivos desenvolvido para o [Namastex FDE / AI Engineer Challenge](https://github.com/namastexlabs/namastex-fde-challenge).

A solução demonstra a integração de um LLM com uma API de cotação deliberadamente instável, mantendo as decisões de negócio determinísticas, testáveis e auditáveis.

## Principais funcionalidades

- Coleta conversacional de informações do cliente em múltiplos turnos.
- Extração semântica estruturada utilizando OpenAI e Pydantic.
- Qualificação determinística e roteamento do workflow com LangGraph.
- Cotações reais obtidas exclusivamente pelo endpoint `POST /quote`.
- Retries limitados, tratamento de timeouts e classificação explícita de erros.
- Invalidação de cotações quando informações relevantes do cliente são alteradas.
- Decisões de handoff humano com códigos de motivo explícitos.
- Logs operacionais estruturados em JSONL e evidências de execução.
- Testes automatizados com dependências HTTP e LLM simuladas.

## Arquitetura

```text
Usuário (CLI)
    |
    v
Extração estruturada com OpenAI
    |  Intenção + campos validados
    v
ConversationService
    |
    v
LangGraph (determinístico)
    |
    +-- Incorporação incremental de informações
    |
    +-- Qualificação
    |     |
    |     +-- Dados ausentes/inválidos -> Solicitar esclarecimento
    |     |
    |     +-- Catálogo indisponível -> Consultar catálogo
    |     |
    |     +-- Dados suficientes -> Solicitar cotação
    |
    +-- QuoteClient (httpx)
    |     |
    |     +-- POST /quote
    |     +-- Timeouts e retries limitados
    |     +-- Validação e classificação tipada de respostas
    |
    +-- Sucesso -> Apresentação determinística da cotação
    +-- Recusa comercial -> Explicação da recusa
    +-- Falha técnica -> Registro de handoff
    +-- Pedido explícito de humano -> Registro de handoff
    |
    v
Auditoria operacional estruturada
```

O LLM interpreta a linguagem do usuário, mas não calcula preços, aprova clientes, executa retries ou define políticas de handoff.

As informações comerciais são apresentadas exclusivamente a partir das respostas validadas do serviço de cotação.

Consulte o documento de [Decisões Arquiteturais](docs/architecture-decisions.md).

## Requisitos

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Docker Desktop com Docker Compose
- Acesso à API da OpenAI e variável `OPENAI_API_KEY`
- Windows PowerShell para executar os comandos abaixo

Não é necessária a instalação global de pacotes Python.

## Configuração e execução

### 1. Instalar as dependências

Na raiz do repositório:

```powershell
uv sync --locked --dev
```

As dependências e suas versões resolvidas estão registradas em `pyproject.toml` e `uv.lock`.

### 2. Configurar a OpenAI

Copie o arquivo de exemplo:

```powershell
Copy-Item .env.example .env
```

Edite o arquivo `.env` localmente e configure `OPENAI_API_KEY`.

A aplicação obtém `OPENAI_API_KEY` exclusivamente das variáveis de ambiente do processo. Ela não carrega arquivos `.env` automaticamente.

Os comandos abaixo utilizam `uv --env-file` para carregar explicitamente o arquivo `.env`.

Como alternativa, utilize uma variável de ambiente já configurada na sessão PowerShell e omita `--env-file .env`.

Nunca inclua `.env`, chaves de API ou outras credenciais em commits.

### 3. Iniciar o serviço de cotação

Certifique-se de que o Docker Desktop esteja em execução:

```powershell
docker compose up -d --build
docker compose ps
```

Verifique o serviço:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

A API disponibiliza os seguintes endpoints:

| Endpoint | Responsabilidade |
|---|---|
| `GET /health` | Verificação básica de disponibilidade |
| `GET /planos` | Catálogo de produtos e regras |
| `POST /quote` | Cálculo autoritativo da cotação |

Por padrão, o serviço de cotação fornecido pelo desafio simula 20% de falhas, 10% de respostas lentas e um atraso de oito segundos nas respostas lentas.

Uma resposta bem-sucedida de `/health` não garante que a próxima cotação será concluída com sucesso.

### 4. Executar o agente interativo

```powershell
uv run --locked --env-file .env python -m autoseguro.cli
```

A CLI preserva o estado da conversa durante a execução do processo.

Exemplo de interação:

```text
Usuário: Quero cotar um seguro para meu carro.

Usuário: Tenho 35 anos e meu carro é de 2022.

Usuário: Meu CEP é 01310-100, quero o plano completo.

Usuário: Na verdade, meu carro é de 2020.

Usuário: Quero falar com um atendente humano.
```

O agente solicita informações ausentes, obtém cotações pela API, invalida cotações anteriores após correções e registra solicitações de handoff.

Digite `sair` para encerrar.

A CLI não possui integração com o WhatsApp nem encaminha efetivamente solicitações a atendentes humanos.

## Qualificação determinística

A aplicação exige as seguintes informações antes de solicitar uma cotação:

- Plano selecionado, validado contra o catálogo da API.
- Idade do cliente.
- Ano-modelo do veículo.
- CEP brasileiro válido, contendo oito dígitos.

O campo `data_inicio` é opcional.

Embora o CEP seja opcional na API fornecida, sua presença é obrigatória na aplicação, pois a região pode alterar o preço do seguro.

A qualificação local verifica a completude e o formato das informações. A elegibilidade comercial é determinada exclusivamente pela API de cotação.

A aplicação nunca calcula o preço do seguro com base no dataset, no preço-base do plano ou em informações geradas pelo LLM.

## Integração de cotação e tratamento de falhas

O cliente HTTP valida as respostas da API utilizando Pydantic.

| Condição | Política |
|---|---|
| HTTP 200 com cotação válida | Aceitar e apresentar a cotação |
| HTTP 422 — recusa comercial | Explicar a recusa, sem retry ou handoff automático |
| HTTP 422 — erro de validação de schema | Erro de integração, sem retry |
| HTTP 400 — payload inválido | Erro de integração, sem retry |
| HTTP 500/502/503/504 | Aplicar retry |
| Falha de conexão ou transporte | Aplicar retry |
| Timeout | Aplicar retry |
| HTTP 200 com resposta inválida | Rejeitar a resposta, sem fabricar uma cotação |

Configuração de retry:

- Máximo de três tentativas, incluindo a solicitação inicial.
- Backoff exponencial: 250 ms e, em seguida, 500 ms.
- Timeout de conexão: 1 segundo.
- Timeout de leitura: 2,5 segundos.
- Timeouts de escrita e pool: 2 segundos cada.

Os timeouts são aplicados individualmente às fases de I/O. Não existe um deadline global rígido para a operação.

Essas configurações de retry são aplicadas a `POST /quote`. A consulta ao catálogo utiliza uma requisição separada, sem o mesmo mecanismo de retry.

A repetição de solicitações é adequada ao endpoint de cotação simulado neste desafio, que não possui efeitos colaterais. Em uma operação de produção com efeitos colaterais, seriam necessárias garantias adicionais de idempotência.

## Política de handoff

| Código de motivo | Condição |
|---|---|
| `user_request` | Solicitação explícita de atendimento humano |
| `quote_unavailable` | Esgotamento dos retries de cotação |
| `catalog_unavailable` | Indisponibilidade do catálogo de produtos |
| `integration_error` | Resposta inválida ou inesperada da integração |
| `out_of_scope` | Solicitação fora do escopo do workflow de cotação |

O handoff representa um estado registrado no sistema, não uma transferência efetivamente realizada para uma pessoa.

Recusas comerciais são distintas de falhas operacionais e não provocam handoff automático.

Depois de registrado, o handoff permanece ativo durante a conversa. A resolução por um atendente humano e a redistribuição do atendimento estão fora do escopo deste MVP.

## Consistência das cotações

Uma cotação bem-sucedida somente pode ser reutilizada quando os dados normalizados da solicitação permanecem inalterados.

Mudanças na idade, no ano do veículo, no plano, no CEP ou na data de início invalidam a cotação ativa anterior.

Correções ambíguas ou inválidas exigem esclarecimento antes da emissão de uma nova cotação. Campos não mencionados nas mensagens posteriores preservam seus valores anteriores.

O prêmio mensal, a franquia, as coberturas, as carências e o primeiro pagamento proporcional, quando retornado, são apresentados a partir da resposta validada da API.

## Testes automatizados

Execute a suíte completa:

```powershell
uv run --locked pytest -q
```

Verifique a sintaxe dos módulos:

```powershell
uv run --locked python -m compileall -q .\autoseguro .\scripts .\tests
```

Os testes abrangem:

- Normalização dos dados do lead e atualizações incrementais.
- Qualificação e invalidação de cotações.
- Classificação de respostas HTTP e erros de integração.
- Retries, timeouts e recuperação de falhas.
- Transições determinísticas do LangGraph.
- Comportamento conversacional multi-turno.
- Preservação de estado na CLI.
- Auditoria JSONL, sanitização e geração de evidências.

A suíte utiliza test doubles e mocks HTTP, sem exigir acesso à OpenAI ou ao Docker.

Ao final da implementação, foram reportados 97 testes aprovados no ambiente local. Execute novamente o comando acima para verificar a versão submetida.

## Reprodução da evidência de execução real

Inicie o serviço Docker e configure a OpenAI conforme as instruções anteriores.

Execute:

```powershell
uv run --locked --env-file .env python -m scripts.demo_conversation
```

O script realiza chamadas reais para extração semântica e cotação, registrando os resultados em:

```text
evidence/demo_conversation.jsonl
```

A demonstração percorre:

1. Solicitação inicial de seguro.
2. Coleta incremental das informações do cliente.
3. Primeira cotação bem-sucedida.
4. Correção das informações do veículo e obtenção de uma segunda cotação.
5. Solicitação explícita de atendimento humano.

A evidência contém as mensagens de entrada, respostas, identificadores de cotação, resultados e transições de estado efetivamente produzidos durante a execução.

Eventuais falhas permanecem registradas. O script não fabrica resultados bem-sucedidos.

O serviço foi projetado para apresentar instabilidade. Uma execução malsucedida pode ser analisada e repetida, mas somente uma execução realmente concluída deve ser apresentada como evidência de sucesso.

**Atenção:** o script de demonstração reinicializa o arquivo de evidência a cada execução. Revise e preserve uma execução validada antes de executá-lo novamente.

## Observabilidade

Os registros operacionais são armazenados em:

```text
logs/operational.jsonl
```

Eles incluem:

- IDs de eventos, conversas, turnos e mensagens.
- Status de processamento das mensagens.
- IDs de cotação, códigos HTTP, números das tentativas e latências.
- Resultados da qualificação e decisões do workflow.
- Status das cotações e dos handoffs, incluindo códigos de motivo.

Cadeia de correlação:

```text
conversation_id -> turn_id -> quote_id -> attempt_number
```

Os logs operacionais não armazenam o conteúdo integral das mensagens, dados pessoais do cliente, credenciais ou valores comerciais.

As evidências sintéticas de demonstração são armazenadas separadamente e podem conter as mensagens da conversa e os payloads da API utilizados na execução.

Os logs operacionais e os dados locais processados são ignorados pelo Git. Somente evidências sintéticas revisadas devem ser incluídas nos commits.

## Dataset e privacidade

O dataset fornecido foi inspecionado antes da implementação:

- 2.500 conversas.
- 26.470 mensagens.
- 1.789 marcadores de mídia sem conteúdo multimodal utilizável.
- Ausência de índices de mensagens duplicados ou faltantes nas conversas inspecionadas.
- Inversões temporais na maioria das conversas.

A reconstrução das conversas deve, portanto, utilizar `message_index`, e não exclusivamente os timestamps.

As colunas auxiliares `conversation_outcome`, `lead_idade_informada` e `veiculo_texto` foram excluídas do contexto do agente, pois podem causar data leakage ao revelar informações que ainda não estavam disponíveis no respectivo turno da conversa.

Para gerar uma versão local com minimização de dados:

```powershell
uv run --locked python -m scripts.sanitize_dataset
```

Arquivo produzido:

```text
local_data/conversations_sanitized.parquet
```

O dataset original não é modificado.

A sanitização por regex contempla formatos comuns de CPF, email, telefone, CEP e placa de veículo. Entretanto, ela não garante anonimização: nomes, formatos incomuns e identificadores contextuais podem permanecer nos dados.

O arquivo sanitizado não deve ser publicado sem revisão adicional.

A integração com a OpenAI utiliza extração estruturada com `store=False`. Essa configuração não constitui garantia de retenção zero de dados pelo provedor. As mensagens atuais do usuário continuam sendo transmitidas à OpenAI para interpretação semântica.

## Estrutura do projeto

```text
autoseguro/
  cli.py                 # Interface interativa de terminal
  conversation.py        # Integração multi-turno e respostas
  extraction.py          # Extração estruturada com OpenAI
  domain.py              # Modelos do lead e merge incremental
  qualification.py       # Validação determinística dos dados
  quote_contracts.py     # Contratos HTTP tipados
  quote_client.py        # HTTP, classificação de erros e retry
  state.py               # Estado da conversa
  workflow.py            # Roteamento com LangGraph
  observability.py       # Auditoria operacional e evidências

scripts/
  inspect_dataset.py
  inspect_quote_api.py
  sanitize_dataset.py
  demo_conversation.py

tests/                   # Testes automatizados
dataset/                 # Dataset e dicionário fornecidos
quote-service/           # API simulada fornecida
docs/                    # Decisões arquiteturais
evidence/                # Evidências sintéticas revisadas
ai-logs/                 # Histórico de desenvolvimento com IA
```

## Escopo e limitações

Este projeto é um MVP desenvolvido para um desafio técnico, não uma plataforma de seguros pronta para produção.

Não foram implementados:

- Integração real com WhatsApp.
- CRM ou encaminhamento efetivo para atendentes humanos.
- Emissão de apólices, cobrança ou contratação de cobertura.
- Persistência das conversas entre reinicializações da CLI.
- Autenticação, autorização ou gerenciamento de segredos em nível de produção.
- Deadlines globais garantidos para as requisições.
- Processamento multimodal real.
- RAG, banco de dados vetorial, fine-tuning ou MCP.
- Monitoramento centralizado e observabilidade de produção.

A CLI mantém o estado da conversa ativa em memória. Os eventos de auditoria operacional são registrados localmente.

O workflow é determinístico após a extração semântica, mas a qualidade da interpretação do LLM pode variar. Os testes verificam o comportamento da aplicação diante de extrações definidas; eles não estabelecem uma garantia estatística de acurácia do modelo.

## Transparência no uso de IA

As conversas de desenvolvimento e suas exportações estão organizadas em [`ai-logs/`](ai-logs/README.md).

Esses registros documentam as decisões arquiteturais, as iterações de implementação, os testes e a revisão do código desenvolvido com assistência de IA.

**Conversa de desenvolvimento no ChatGPT:**

[Histórico de arquitetura, implementação, testes e preparação da entrega](https://chatgpt.com/share/6ac6ee58-ad28-83e8-8bfa-0f43687ff4f0)

Credenciais e informações pessoais devem ser revisadas e removidas dos registros antes da publicação.

## Referências

- [Repositório original do desafio](https://github.com/namastexlabs/namastex-fde-challenge)
- [Decisões arquiteturais](docs/architecture-decisions.md)
- [Histórico de desenvolvimento com IA](ai-logs/README.md)
- [Conversa de desenvolvimento no ChatGPT](https://chatgpt.com/share/6ac6ee58-ad28-83e8-8bfa-0f43687ff4f0)
- [Evidência de execução](evidence/demo_conversation.jsonl)
