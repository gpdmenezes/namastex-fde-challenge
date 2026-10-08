
# Histórico de Desenvolvimento Assistido por IA

Este diretório reúne os registros das conversas com ferramentas de inteligência artificial utilizadas durante o desenvolvimento do AutoSeguro para o desafio técnico de Forward Deployed Engineer / AI Engineer da Namastex.

Esses registros fazem parte dos requisitos de transparência da entrega e permitem acompanhar como a IA foi utilizada ao longo do processo de engenharia.

## Conteúdo dos registros

Devem ser incluídas as conversas completas das ferramentas de IA efetivamente utilizadas no projeto, abrangendo:

- Análise dos requisitos e decisões arquiteturais.
- Discussões sobre implementação e depuração.
- Falhas de testes, correções e revisões de decisões técnicas.
- Preparação da documentação e da entrega final.

Os registros podem ser disponibilizados em Markdown, JSONL, arquivos de texto exportados ou links públicos de compartilhamento, quando apropriado.

## Conversa de desenvolvimento — ChatGPT

A conversa principal utilizada no desenvolvimento do projeto está disponível no link abaixo:

[**Acessar conversa de desenvolvimento no ChatGPT**](https://chatgpt.com/share/6ac6ee58-ad28-83e8-8bfa-0f43687ff4f0)

O histórico contempla o processo de desenvolvimento, incluindo:

1. **Análise inicial:** interpretação dos requisitos, identificação de riscos e avaliação das alternativas arquiteturais.
2. **Discovery:** inspeção do dataset e validação empírica dos contratos, limites e comportamentos da Quote API.
3. **Modelagem de domínio:** definição dos contratos Pydantic, qualificação e gerenciamento do estado conversacional.
4. **Integração HTTP:** implementação de retries, timeouts e classificação de erros comerciais e técnicos.
5. **Orquestração:** construção do workflow determinístico utilizando LangGraph.
6. **Integração com LLM:** extração estruturada de informações, interpretação de intenções e tratamento de mensagens multi-turno.
7. **Execução ponta a ponta:** integração da CLI com a OpenAI e o quote-service real.
8. **Observabilidade e privacidade:** implementação de logs estruturados, sanitização de dados e geração de evidências.
9. **Preparação da entrega:** documentação, revisão dos artefatos e auditoria do repositório.

O histórico também registra justificativas técnicas, trade-offs, hipóteses, resultados de testes e ajustes realizados ao longo da implementação.

## Privacidade e segurança

Antes de publicar registros ou exportações:

1. Remova chaves de API, tokens e quaisquer outras credenciais.
2. Oculte informações pessoais e caminhos locais que possam conter dados sensíveis.
3. Verifique blocos de código e saídas de ferramentas em busca de credenciais.
4. Confira se links de compartilhamento não expõem informações que deveriam permanecer privadas.
5. Preserve, sempre que possível, o raciocínio técnico, as decisões arquiteturais e o contexto das alterações.

Os registros não devem ser editados com a finalidade de ocultar erros, abordagens malsucedidas ou decisões posteriormente revisadas.

As remoções devem se limitar, preferencialmente, a informações confidenciais, pessoais ou sensíveis.

## Inventário das exportações

As exportações efetivamente adicionadas a este diretório devem ser relacionadas nesta seção, identificando a ferramenta utilizada e o respectivo arquivo ou link.

### ChatGPT

- [Conversa principal de desenvolvimento — AutoSeguro FDE / AI Engineer Challenge](https://chatgpt.com/share/6ac6ee58-ad28-83e8-8bfa-0f43687ff4f0)

Caso outras ferramentas de IA tenham sido utilizadas, seus históricos deverão ser adicionados ao inventário após a exportação e revisão.

Não devem ser relacionados como exportados arquivos que ainda não estejam disponíveis.

## Finalidade para avaliação

Os registros permitem avaliar a utilização da IA como ferramenta de apoio à engenharia, incluindo a formulação de hipóteses, a avaliação de alternativas, a implementação incremental, a identificação de falhas e a validação das soluções propostas.

O histórico de desenvolvimento não substitui os artefatos técnicos do projeto.

O código-fonte, os testes automatizados, a documentação das decisões arquiteturais e as evidências de execução estão disponíveis separadamente no repositório.
