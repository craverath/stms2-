# PRD — STMS

## 1. Controle do documento

| Campo | Valor |
| --- | --- |
| Produto | STMS |
| Status | Aprovado para implementação do MVP |
| Plataforma inicial | macOS e Linux |
| Linguagem | Python 3.12+ |
| Licença | MIT |
| Interface | CLI interativa |
| Persistência | SQLite, JSON e Markdown locais |

## 2. Resumo

STMS é um orquestrador local de desenvolvimento assistido por agentes. A partir
de uma demanda em texto ou de um PRD, ele conduz uma entrevista de planejamento,
solicita aprovação humana, implementa uma task DAG em worktrees isoladas, executa
testes determinísticos, realiza revisões progressivas e, após o aceite humano,
integra a mudança na branch de origem.

O fluxo separa decisões semânticas, tomadas por agentes ou pessoas, de operações
determinísticas como transições de estado, Git, execução de testes, persistência,
retries e aplicação das políticas de revisão.

O sistema deve favorecer mudanças futuras. Agentes, harnesses, persistência,
sandbox, interface terminal, execução de testes e registro de eventos dependem de
contratos estáveis e podem ser substituídos sem alterar as regras centrais.

## 3. Contexto e problema

Ferramentas de coding agents aceleram planejamento e implementação, mas seus
fluxos nativos variam em sessões, permissões, modelos, saídas estruturadas e
isolamento. Também não garantem, por si só, que uma demanda siga sempre a mesma
sequência de aprovação, teste, correção e revisão.

O STMS deve resolver quatro problemas:

1. Converter uma solicitação incompleta em um plano aprovado e rastreável.
2. Permitir trabalho paralelo sem misturar alterações no checkout do usuário.
3. Aplicar testes e políticas de revisão fora do controle discricionário do
   modelo.
4. Preservar estado suficiente para retomar o fluxo após interrupções ou troca de
   harness.

O documento de [evidências e métodos](contexto/evidencias-e-metodos.md) reforça
gates humanos, lotes pequenos, controle de versão, contexto preciso e medição do
fluxo completo, não apenas da quantidade de código produzida.

## 4. Objetivos

### 4.1 Objetivos do MVP

- Iniciar uma demanda pela raiz de um repositório Git.
- Oferecer uma entrevista conversacional com o agente planejador.
- Exigir aprovação humana explícita antes da implementação.
- Produzir `plan.md` e `context.md` para cada execução.
- Representar o plano como uma task DAG estruturada e validada.
- Executar tasks independentes concorrentemente em worktrees.
- Exigir que o implementador crie somente os testes essenciais da demanda.
- Executar testes por um componente determinístico.
- Retornar falhas de teste automaticamente ao implementador.
- Aplicar até quatro rodadas condicionais de revisão.
- Pausar e retomar o workflow sem depender da sessão do provedor.
- Exigir aceite humano antes do merge.
- Fazer squash e merge automático na branch original após o aceite.
- Emitir eventos locais estruturados para métricas futuras.
- Permitir Codex e Claude Code como harnesses suportados.
- Oferecer Pi como adapter experimental.
- Permitir novos agentes e adapters sem modificar agentes existentes.

### 4.2 Resultados esperados

- O usuário acompanha todo o ciclo em uma única experiência de terminal.
- A branch original não recebe alterações dos agentes antes do aceite final.
- Nenhum teste é considerado aprovado com base apenas na afirmação de um agente.
- Um processo interrompido pode continuar do último checkpoint seguro.
- As decisões humanas, comandos executados e transições ficam auditáveis.

## 5. Fora do escopo do MVP

- Windows.
- Servidor multiusuário ou workers distribuídos.
- Pull requests, push, CI/CD e deploy.
- Dashboard ou envio externo de telemetria.
- Marketplace de plugins.
- Workflow inteiramente editável por YAML.
- Metas de cobertura de testes.
- Criação indiscriminada de testes para código fora da demanda.
- Lint, typecheck e build como gates obrigatórios.
- Instalação de dependências que não façam parte do plano aprovado.
- Publicação obrigatória do pacote no PyPI.

## 6. Usuário e jornada principal

### 6.1 Usuário-alvo

Desenvolvedor que trabalha em um repositório Git local e possui ao menos um dos
harnesses suportados instalado e autenticado.

### 6.2 Pré-condições

- O comando é executado na raiz do repositório atendido.
- O repositório possui `HEAD` válido e uma branch atualmente selecionada.
- Não existem alterações rastreadas e não commitadas.
- Arquivos não rastreados são permitidos.
- Existe um `stms.yml` válido na raiz.
- O sandbox e o harness selecionados passam pelo preflight.
- Apenas um run pode estar ativo por repositório.

### 6.3 Início por prompt

```shell
stms start "crie a tela de login"
```

### 6.4 Início por documento

```shell
stms start --file PRD.md
```

### 6.5 Retomada

```shell
stms resume <run-id>
stms resume
```

Sem `run-id`, o comando seleciona o run retomável mais recente do repositório.
Ao retomar, o usuário também pode encerrar definitivamente o run, preservando seu
estado.

## 7. Fluxo funcional

```text
START
  -> PREFLIGHT
  -> INTERVIEWING
  -> PLAN_PENDING_APPROVAL
       -> feedback -> INTERVIEWING
       -> approve  -> IMPLEMENTING
  -> TESTING
       -> failed -> IMPLEMENTING
       -> passed -> REVIEWING
  -> REVIEWING
       -> blocking findings -> IMPLEMENTING -> TESTING -> REVIEWING
       -> review 4 high finding -> HUMAN_ESCALATION
       -> accepted by policy -> FINAL_APPROVAL
  -> FINAL_APPROVAL
       -> adjustments -> IMPLEMENTING -> TESTING -> review 1
       -> replan -> REPLANNING -> INTERVIEWING
       -> abort -> FAILED
       -> approve -> MERGING
  -> MERGING
       -> changed base -> PAUSED
       -> success -> COMPLETED
```

### 7.1 Preflight

Antes de criar o run, o STMS deve validar:

- diretório atual igual à raiz Git;
- `HEAD`, branch-base e ausência de alterações rastreadas;
- ausência de outro run ativo;
- validade e versão do `stms.yml`;
- presença e versão compatível dos harnesses selecionados;
- autenticação dos harnesses;
- disponibilidade do modelo e do nível de effort;
- disponibilidade e capacidades do sandbox;
- configuração Git de nome e e-mail;
- validade dos comandos de teste, quando já estiverem configurados.

O STMS não instala harnesses, não autentica contas e não cria commits para sanar
pré-condições. Erros devem indicar uma ação corretiva.

### 7.2 Entrevista do planejador

- O planejador recebe a solicitação inicial, acesso somente leitura à codebase e
  ao `stms.yml`.
- As perguntas são apresentadas em pequenos grupos de até três questões
  relacionadas.
- O usuário responde em texto natural no terminal.
- O planejador retorna `needs_input` enquanto houver decisões relevantes abertas.
- Depois de dez turnos sem produzir um plano, o STMS abre um gate para continuar,
  reformular a solicitação ou abortar.
- O planejador declara quando possui contexto suficiente, mas não aprova seu
  próprio plano.
- Quando pesquisar na web, registra em `context.md` as conclusões relevantes e as
  URLs consultadas.

### 7.3 Produção do plano

O planejador deve produzir uma resposta estruturada validada por Pydantic e duas
projeções Markdown.

`plan.md` deve conter:

- objetivo e resultado esperado;
- escopo e itens explicitamente fora do escopo;
- decisões humanas e suposições;
- critérios de aceitação observáveis;
- riscos e dependências;
- comandos de teste propostos;
- tasks ordenadas;
- dependências entre tasks;
- indicação de execução sequencial ou paralela;
- áreas e arquivos provavelmente afetados;
- testes essenciais esperados por task.

`context.md` deve conter somente informações úteis aos agentes seguintes:

- arquitetura e convenções relevantes;
- arquivos e símbolos importantes;
- restrições do repositório;
- decisões tomadas durante a entrevista;
- fatos obtidos por pesquisa e suas fontes;
- arquivos não rastreados aprovados para cópia.

A transcrição completa da entrevista não integra esses dois artefatos. Os agentes
seguintes continuam podendo consultar a codebase quando precisarem.

### 7.4 Aprovação do plano

O usuário pode:

- aprovar o plano;
- fornecer um novo prompt ao planejador;
- abortar.

Um novo prompt retorna ao planejador. A nova versão sobrescreve `plan.md` e
`context.md`. O histórico permanece nos checkpoints e eventos, não em arquivos
`plan.vN.md`.

Somente após a aprovação os comandos de teste, dependências, arquivos não
rastreados e permissões propostos ficam congelados para a execução.

### 7.5 Implementação

- `implementer.py` representa o papel do implementador.
- Cada task recebe uma sessão isolada do mesmo agente.
- A sessão recebe apenas a task, o plano aprovado, o contexto necessário e acesso
  à codebase de sua worktree.
- Tasks sem dependências pendentes formam uma onda de execução.
- Todas as tasks de uma onda partem do mesmo commit da branch de integração.
- A concorrência respeita `max_parallel_tasks`, cujo padrão é `2`.
- O implementador cria primeiro os testes essenciais da task e depois implementa
  o comportamento.
- O resultado estruturado identifica arquivos modificados, testes criados,
  comandos sugeridos e riscos encontrados.
- O implementador não altera o plano nem amplia o escopo unilateralmente.
- Uma descoberta que exige mudança material volta ao gate humano ou ao
  planejador.

### 7.6 Integração de tasks

- O STMS cria uma branch de integração e uma branch/worktree por task.
- O componente determinístico, não o agente, realiza stage e commits temporários.
- Tasks concluídas são integradas de forma serializada na branch de integração.
- Conflitos retornam ao implementador responsável, que trabalha com o conflito e
  o estado integrado.
- Depois de três falhas de resolução, o workflow pausa para decisão humana.
- Somente depois da integração da onda seguinte suas tasks dependentes podem
  iniciar.

### 7.7 Testes determinísticos

O `TestRunner` é separado dos agentes e decide o resultado por exit code, timeout
e política configurada.

Fluxo:

1. Rodar os testes focados da task antes de integrá-la.
2. Integrar todas as tasks aprovadas.
3. Rodar toda a suíte existente, incluindo os testes novos.
4. Em falha, abrir uma sessão de correção na worktree de integração com logs e
   diff completo.
5. Rodar novamente a suíte depois de cada correção.
6. Após três correções sem sucesso na mesma etapa, pausar para o humano.

O projeto atendido não precisa possuir testes prévios. O implementador cria
somente os testes necessários para as tasks solicitadas. Se houver uma suíte
anterior, ela é executada para detectar regressões.

Cada comando registra:

- argumentos;
- diretório de trabalho;
- ambiente não sensível permitido;
- horário e duração;
- exit code ou sinal;
- timeout;
- stdout e stderr;
- indicação de truncamento.

O timeout padrão é de 15 minutos por comando e pode ser alterado no `stms.yml`.
Ao expirar, o runner encerra também os processos filhos.

### 7.8 Revisão

Cada rodada usa uma sessão nova do revisor. Ela recebe:

- objetivo e critérios de aceitação;
- `plan.md` e `context.md`;
- diff entre a base e a integração;
- resultados completos dos testes;
- achados bloqueantes anteriores;
- codebase integrada em modo somente leitura.

Cada achado estruturado possui identificador, severidade, evidência, localização
quando aplicável e correção sugerida.

Severidades padrão, substituíveis pelo `stms.yml`:

- `high`: requisito central não atendido, vulnerabilidade, perda ou corrupção de
  dados, suíte quebrada ou código inutilizável;
- `medium`: erro funcional relevante, caso importante não coberto ou problema
  significativo de manutenção;
- `low`: melhoria pequena que não afeta o comportamento principal.

As rodadas são condicionais:

| Rodada | Retorna ao implementador quando encontra | Próximo passo sem achado bloqueante |
| --- | --- | --- |
| 1 | `high`, `medium` ou `low` | Gate final |
| 2 | `high` ou `medium` | Gate final |
| 3 | `high` | Gate final |
| 4 | Não retorna automaticamente | Gate final ou escalação humana |

- A rodada seguinte só existe após uma correção exigida pela anterior.
- Depois de cada correção, toda a suíte é executada novamente.
- Achados que não bloqueiam na rodada corrente não são carregados adiante nem
  apresentados no relatório final do MVP.
- Se a quarta rodada encontrar um achado `high`, o humano pode replanejar no
  mesmo run ou encerrar com estado `FAILED`.
- No replanejamento, o planejador recebe o código atual e os achados. `plan.md` e
  `context.md` são sobrescritos, e o histórico continua no estado operacional.

### 7.9 Gate final

Depois de uma revisão aceita pela política, o humano pode:

- aprovar e iniciar o merge;
- enviar ajustes ao implementador;
- voltar ao planejador;
- abortar.

Ajustes do implementador executam novamente testes e reiniciam a revisão na
rodada 1. Um retorno ao planejador reinicia planejamento, implementação, testes e
revisão dentro do mesmo run.

### 7.10 Merge e conclusão

- A branch original é comparada com o commit-base registrado no início.
- Se ela mudou, o STMS não faz rebase ou merge silencioso: pausa e abre um gate.
- Após aprovação e base inalterada, o STMS produz um único commit squash.
- Autor e e-mail vêm da configuração Git do usuário.
- Mensagem padrão: `stms: <resumo da demanda> [<run-id>]`.
- O commit é integrado automaticamente na branch original.
- O STMS remove worktrees e branches temporárias depois do sucesso.
- `.stms/estado/<run-id>` permanece como registro local.
- A entrada `.stms/estado/` é incluída no `.gitignore` pela própria mudança de
  integração e chega à branch original somente no merge final.

## 8. Estados

Estados principais:

- `INTERVIEWING`
- `PLAN_PENDING_APPROVAL`
- `IMPLEMENTING`
- `TESTING`
- `REVIEWING`
- `FINAL_APPROVAL`
- `MERGING`
- `COMPLETED`

Estados auxiliares:

- `PAUSED`
- `REPLANNING`
- `FAILED`

Todo estado deve registrar fase, subfase, task atual, rodada de revisão, tentativa,
última transição e próximos eventos permitidos. Transições inválidas falham antes
de qualquer efeito externo.

## 9. Persistência e artefatos

### 9.1 Estrutura por run

```text
.stms/
└── estado/
    └── <run-id>/
        ├── checkpoint.sqlite
        ├── state.json
        ├── plan.md
        ├── context.md
        ├── events.jsonl
        ├── tests/
        │   └── <attempt-id>.log
        └── reviews/
            └── <round-id>.json
```

### 9.2 Fonte de verdade

- SQLite é a fonte operacional para checkpoints do workflow.
- `state.json` é um snapshot legível do último checkpoint concluído.
- Markdown é uma projeção humana, não o mecanismo de transição.
- IDs de sessão dos provedores são referências auxiliares.
- Se uma sessão não puder ser retomada, o adapter inicia outra usando o estado,
  plano e contexto persistidos.

### 9.3 Versionamento e compatibilidade

Cada run registra:

- versão do schema de estado;
- versão do workflow;
- digest da configuração aprovada;
- versão/digest dos prompts;
- adapters e versões efetivas;
- harness, modelo e effort efetivos;
- commit-base e branch-base.

Um `resume` incompatível deve parar com erro acionável. Migração automática de
schema fica fora do MVP.

### 9.4 Concorrência e idempotência

- Um lock persistente por repositório contém PID, `run-id` e timestamp.
- O STMS detecta locks órfãos antes da retomada.
- Checkpoints ocorrem antes e depois de efeitos externos.
- Cada invocação, comando, integração e commit recebe um `operation_id`.
- Uma retomada não repete operações confirmadas como concluídas.

O [LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence) será
usado atrás de `WorkflowEngine`, com armazenamento SQLite. Nenhum agente importa
LangGraph diretamente.

## 10. Arquitetura

### 10.1 Princípios

- Agentes possuem uma única responsabilidade semântica.
- Regras de negócio dependem de protocolos, não de SDKs ou CLIs específicos.
- Implementações de portas podem ser substituídas preservando seus contratos.
- Novos agentes podem ser inseridos na composição sem modificar agentes
  existentes.
- Eventos de métricas são observadores do fluxo e não controlam transições.
- Git e testes não ficam sob autoridade direta de modelos.

### 10.2 Portas principais

```python
AgentHarness
WorkflowEngine
CheckpointStore
ArtifactStore
WorktreeManager
TestRunner
SandboxRuntime
EventSink
PromptProvider
PromptPort
EventRenderer
```

Cada porta deve expor tipos explícitos e erros de domínio acionáveis. Metadados
específicos de fornecedor ficam encapsulados pelo adapter.

### 10.3 Estrutura proposta

```text
src/stms/
├── cli/
│   ├── app.py
│   ├── interaction.py
│   └── renderer.py
├── agents/
│   ├── planner.py
│   ├── implementer.py
│   └── reviewer.py
├── application/
│   ├── orchestrator.py
│   ├── workflow.py
│   └── services.py
├── domain/
│   ├── models.py
│   ├── states.py
│   ├── policies.py
│   ├── events.py
│   └── ports.py
├── deterministic/
│   ├── process_runner.py
│   ├── test_runner.py
│   └── worktree_manager.py
├── adapters/
│   ├── harnesses/
│   │   ├── codex.py
│   │   ├── claude.py
│   │   └── pi.py
│   ├── persistence/
│   ├── sandbox/
│   └── terminal/
└── composition.py

tests/
├── unit/
├── integration/
├── e2e/
└── conformance/
```

Cada arquivo em `agents/` contém a lógica do papel, seu prompt padrão e o contrato
de saída. O `PromptProvider` permite substituir esse prompt sem alterar o agente.
A composição do fluxo fica fora dos agentes.

## 11. Harnesses

### 11.1 Contrato comum

O contrato deve suportar:

- iniciar e retomar sessão;
- enviar turnos;
- cancelar;
- fornecer `cwd` absoluto;
- selecionar modelo e effort;
- definir política de ferramentas;
- emitir eventos normalizados;
- retornar saída estruturada;
- reportar uso quando disponível;
- aplicar timeout e limite de turnos.

Eventos mínimos:

- `session_started`
- `message_delta`
- `tool_started`
- `tool_completed`
- `user_input_requested`
- `usage_updated`
- `run_completed`
- `run_failed`

Payloads brutos ficam desabilitados por padrão. Não se deve persistir raciocínio
interno do modelo.

### 11.2 Codex

O adapter deve usar o [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk) como
transporte principal, validar capacidades do modelo e manter sessões como estado
auxiliar.

### 11.3 Claude Code

O adapter deve usar o [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/python),
normalizar permissões e tratar diferenças entre effort, sessões e eventos.

### 11.4 Pi

O adapter Pi usa o [RPC JSONL bidirecional](https://pi.dev/docs/latest/rpc) por
subprocesso. Ele é experimental no MVP porque não oferece sandbox ou resposta
final por JSON Schema nativos. Saídas são validadas e, quando necessário,
reparadas pelo adapter.

### 11.5 Validação de saída

Respostas sem conformidade com o modelo Pydantic são reenviadas para reparo até
duas vezes. Depois disso, o workflow pausa para decisão humana. Texto livre nunca
substitui silenciosamente um contrato inválido.

## 12. Configuração

O `stms.yml` é obrigatório na raiz do projeto atendido. Sua ausência retorna um
erro com um exemplo completo, mas não cria o arquivo automaticamente.

Exemplo indicativo:

```yaml
version: 1

agents:
  planner:
    harness: codex
    model: "<model-id>"
    effort: high
    prompt: null
    timeout_seconds: 1800
    max_turns: 20
  implementer:
    harness: claude
    model: "<model-id>"
    effort: high
    prompt: null
    timeout_seconds: 3600
    max_turns: 50
  reviewer:
    harness: codex
    model: "<model-id>"
    effort: high
    prompt: null
    timeout_seconds: 1800
    max_turns: 20

workflow:
  max_parallel_tasks: 2
  infrastructure_retries: 2
  implementation_retries: 3
  structured_output_retries: 2

tests:
  timeout_seconds: 900
  commands: []

review:
  severities:
    high: "Core requirement, security, data loss, broken suite or unusable code"
    medium: "Relevant behavior, important case or significant maintainability issue"
    low: "Small improvement without impact on core behavior"
  blocking:
    round_1: [high, medium, low]
    round_2: [high, medium]
    round_3: [high]
    round_4: []
  escalate:
    round_4: [high]

security:
  sandbox: srt
  allow_native_fallback: false
  planner_web: true
  test_network: false
```

Os IDs de modelo são strings opacas. Cada adapter valida em runtime se o modelo e
o effort são compatíveis. Configuração não suportada falha; não há downgrade
silencioso.

Prompts personalizados podem ser referenciados por caminho, por exemplo
`.stms/prompts/planner.md`. O prompt padrão permanece no módulo do agente.

## 13. Descoberta de testes

Precedência:

1. Comandos explícitos em `stms.yml`.
2. Comandos documentados nos arquivos do projeto ou na configuração de CI.
3. Detecção baseada nos manifestos da stack.
4. Proposta do planejador, incluída no plano para aprovação humana.

Depois da aprovação, os comandos são imutáveis durante aquele ciclo. Uma mudança
necessária volta ao gate de planejamento.

Comandos são representados como listas de argumentos e executados sem shell. O
uso de `shell: true` exige configuração explícita e aprovação no plano. O STMS usa
o ambiente já definido pelo projeto atendido e não cria automaticamente um
virtualenv para ele.

## 14. Git e worktrees

### 14.1 Convenções

```text
stms/<run-id>/integration
stms/<run-id>/task-<task-id>
```

### 14.2 Regras

- Todas as worktrees derivam da branch ativa no início do run.
- O commit-base é imutável e registrado no estado.
- Agentes não executam stage, commit, merge, rebase ou push.
- O `WorktreeManager` é a única autoridade para mutações Git.
- Nenhum push é realizado.
- O merge final só ocorre após aprovação humana explícita.

### 14.3 Arquivos não rastreados

O planejador pode ler arquivos não rastreados. Eles só são copiados para
worktrees quando declarados no plano aprovado e após validação de:

- padrões sensíveis;
- tamanho máximo;
- links simbólicos que escapem do repositório;
- destino da cópia.

Segredos, credenciais e artefatos excessivamente grandes são recusados.

## 15. Segurança e sandbox

O backend padrão é o [Anthropic Sandbox Runtime](https://github.com/anthropics/sandbox-runtime),
invocado por subprocesso atrás de `SandboxRuntime`. Ele suporta macOS e Linux,
não exige daemon e permite políticas de filesystem e rede. Como ainda é beta, o
adapter deve validar suas capacidades efetivas e falhar fechado.

### 15.1 Política por papel

| Papel | Filesystem | Rede de ferramentas | Git |
| --- | --- | --- | --- |
| Planejador | Codebase somente leitura | Provider e pesquisa aprovada | Somente leitura |
| Implementador | Escrita apenas na worktree | Somente provider | Nenhuma mutação direta |
| Revisor | Codebase integrada somente leitura | Somente provider | Somente leitura |
| TestRunner | Worktree conforme comando | Negada por padrão | Nenhuma mutação direta |

Instalações de dependências previstas no plano recebem uma política temporária e
restrita aos domínios necessários.

Se `srt` não estiver disponível, o STMS pode usar o sandbox nativo do harness
somente quando `stms.yml` permitir explicitamente. Nunca há fallback silencioso
para execução sem isolamento.

Configurações de sandbox são geradas fora da worktree. Segredos são passados por
referência e removidos de logs. O STMS nunca deve registrar valores de tokens,
credenciais ou variáveis sensíveis.

## 16. CLI e experiência no terminal

- Typer implementa comandos e validação.
- `prompt_toolkit` mantém a conversa assíncrona do planejador.
- Rich apresenta Markdown, progresso, tasks, worktrees, testes e revisões.
- Somente um componente controla a linha de entrada por vez.
- Atualizações concorrentes não podem corromper o prompt ativo.
- Conteúdo de agentes é renderizado como texto, sem interpretar markup externo.
- Fora de TTY, animações são desabilitadas.

O primeiro `Ctrl-C` solicita pausa segura e aguarda o checkpoint atual. O segundo
força o encerramento. Um run interrompido de forma segura pode ser retomado.

### 16.1 Comandos do MVP

- `stms start`
- `stms resume`

### 16.2 Códigos de saída

| Código | Significado |
| --- | --- |
| `0` | Concluído e integrado |
| `1` | Falha terminal |
| `2` | Entrada ou configuração inválida |
| `3` | Pausado e retomável |
| `130` | Interrompido pelo usuário |

## 17. Falhas e retries

| Falha | Política |
| --- | --- |
| Teste da implementação | Volta ao implementador; máximo de 3 correções por etapa |
| Conflito entre tasks | Volta ao implementador; máximo de 3 correções |
| Timeout ou indisponibilidade de harness | 2 retries com espera progressiva; depois pausa |
| Saída estruturada inválida | 2 tentativas de reparo; depois pausa |
| Sessão do provedor perdida | Cria nova sessão a partir do estado persistido |
| Sandbox requerido indisponível | Falha fechada ou fallback explicitamente autorizado |
| Branch-base alterada | Pausa antes do merge |
| Schema incompatível no resume | Falha com diagnóstico; não migra automaticamente |

Retries de infraestrutura não avançam a rodada de revisão nem contam como falha
de implementação.

## 18. Eventos e métricas futuras

O MVP persiste eventos normalizados localmente em `events.jsonl`. Não envia
telemetria.

Campos mínimos:

- `event_id`;
- `run_id`;
- `task_id`, quando aplicável;
- fase e estado;
- tentativa e rodada;
- timestamp;
- duração;
- resultado;
- harness, modelo e versão;
- tokens e custo, quando fornecidos de forma confiável;
- referência para logs ou artefatos.

Eventos brutos de provedores ficam desabilitados por padrão. O `EventSink` deve
permitir futura integração com métricas sem modificar agentes ou regras do
workflow.

Métricas futuras previstas incluem lead time, tempo por fase, ciclos de revisão,
retrabalho, falhas de teste, verification tax, tokens, custo e taxa de conclusão.

## 19. Requisitos não funcionais

### 19.1 Confiabilidade

- Checkpoint antes e depois de toda fronteira externa.
- Transições atômicas e validadas.
- Operações externas idempotentes ou reconciliáveis.
- Recuperação após encerramento inesperado.
- Nenhuma conclusão declarada sem teste e revisão efetivamente executados.

### 19.2 Segurança

- Menor privilégio por papel.
- Sandbox fail-closed.
- Rede negada por padrão para ferramentas.
- Segredos fora de prompts, estado e logs.
- Nenhum push ou deploy.
- Validação de paths, symlinks e arquivos não rastreados.

### 19.3 Manutenibilidade

- Tipos explícitos nas fronteiras públicas.
- Modelos Pydantic versionados.
- Dependências invertidas por protocolos.
- Agentes sem conhecimento de CLI, Git, SQLite ou LangGraph.
- Configuração de prompts e severidades sem mudanças na lógica central.

### 19.4 Reprodutibilidade

- `pyproject.toml` e `uv.lock` versionados.
- Versões efetivas registradas por run.
- Comandos de teste congelados após aprovação.
- Modelo e effort validados sem fallback silencioso.

## 20. Estratégia de testes do STMS

### 20.1 Testes unitários

Cobrir somente comportamento determinístico relevante:

- transições válidas e inválidas;
- política das quatro revisões;
- scheduler e dependências da task DAG;
- limites de retries;
- validação do `stms.yml`;
- schemas Pydantic;
- filtragem de eventos e segredos;
- construção de comandos sem shell;
- códigos de saída.

### 20.2 Testes de integração

Usar repositórios Git temporários e harnesses falsos para validar:

- criação e integração de worktrees;
- conflitos;
- commits temporários e squash final;
- execução e timeout de subprocessos;
- checkpoints SQLite;
- pausa, lock órfão e retomada;
- logs e artefatos.

### 20.3 Testes E2E

Manter um ou dois fluxos críticos com harness falso:

1. `start` -> entrevista -> aprovação -> implementação -> testes -> revisão ->
   aceite -> merge.
2. Falha de teste ou interrupção -> correção/retomada -> conclusão.

### 20.4 Conformidade de harness

Codex, Claude e Pi possuem testes manuais opcionais, fora da suíte automática.
Eles verificam autenticação, sessão, streaming, cancelamento, output estruturado,
permissões e comportamento no sandbox. Testes com modelos reais não são gates da
suíte determinística.

## 21. Critérios de aceite do MVP

O MVP está aceito quando:

1. É instalável diretamente do Git com `uv tool install` e expõe `stms`.
2. `stms start` aceita texto e `--file` somente na raiz de um repositório válido.
3. A entrevista suporta vários turnos e feedback sobre o plano.
4. `plan.md`, `context.md`, SQLite, snapshot e eventos são persistidos.
5. O plano estruturado representa dependências e paralelismo.
6. Duas tasks independentes podem executar em worktrees simultâneas.
7. Tasks dependentes só iniciam após integração da dependência.
8. Testes focados e suíte completa são executados por `TestRunner`.
9. Uma falha comprovadamente retorna ao implementador.
10. A política das quatro revisões segue exatamente os limiares configurados.
11. Uma alteração solicitada no gate final reinicia a revisão na rodada 1.
12. `Ctrl-C` seguro e `stms resume` recuperam o run.
13. Perda da sessão do provedor não perde o estado do workflow.
14. O sandbox aplica permissões distintas por papel.
15. A branch original permanece intacta até o aceite.
16. O aceite produz um único commit squash e remove recursos temporários.
17. Uma branch-base alterada não recebe merge automático.
18. A suíte unitária, integrações essenciais e E2E falsos passam.

## 22. Documentação e onboarding

O README deve permitir que um novo usuário instale e use o STMS sem conhecer sua
arquitetura interna. Deve incluir:

1. Requisitos de sistema para macOS e Linux.
2. Instalação do `uv`.
3. Instalação diretamente do repositório Git.
4. Instalação e autenticação de Codex e Claude.
5. Instalação e validação do sandbox.
6. Criação manual do primeiro `stms.yml`.
7. Exemplos mínimos por harness.
8. Primeiro uso com prompt e arquivo.
9. Explicação da entrevista e dos gates humanos.
10. Localização do estado e dos logs.
11. Retomada de um run pausado.
12. Funcionamento de branches, worktrees e merge final.
13. Solução dos erros de preflight mais comuns.
14. Limitações e itens fora do MVP.

A documentação técnica deve estar em inglês no MVP. A arquitetura deve permitir
mensagens localizadas na CLI futuramente.

## 23. Plano de entrega sugerido

### Fase 1 — Domínio e persistência

- Modelos, estados, eventos e políticas.
- Validação de configuração.
- `WorkflowEngine` e checkpoints SQLite.
- Unit tests determinísticos.

### Fase 2 — Operações locais

- Process runner.
- Test runner.
- Worktree manager.
- Locks, idempotência e artefatos.
- Testes de integração com repositórios temporários.

### Fase 3 — Agentes e adapters

- Planner, implementer e reviewer.
- Codex adapter.
- Claude adapter.
- Pi adapter experimental.
- Contratos de eventos e outputs estruturados.

Codex e Claude podem ser implementados em paralelo depois que o contrato comum
estiver estável. Pi depende primeiro da conclusão do sandbox e da validação do
RPC.

### Fase 4 — CLI e gates humanos

- `start` e `resume`.
- Entrevista interativa.
- Renderização de progresso.
- Aprovação do plano, escalações e gate final.

### Fase 5 — Segurança e fluxo completo

- SRT sandbox.
- Políticas por papel e rede.
- Workflow de revisão completo.
- Merge squash e limpeza.
- E2E com harness falso.

### Fase 6 — Onboarding e conformidade

- README e exemplo de configuração.
- Guia de novos agentes/adapters.
- Testes manuais de conformidade dos harnesses.
- Validação final dos critérios de aceite.

## 24. Riscos e mitigação

| Risco | Mitigação |
| --- | --- |
| Mudança de formato ou capacidade dos harnesses | Adapter, preflight, versões registradas e testes de conformidade |
| SRT ainda beta | `SandboxRuntime`, fail-closed e fallback nativo somente explícito |
| Conflitos em tasks paralelas | Ondas, integração serializada e retorno ao implementador |
| Loop de correções | Limites de retries e gates humanos |
| Estado incompatível após atualização | Versionamento e recusa de resume incompatível |
| Vazamento de segredo em contexto ou log | Redação, eventos normalizados e payload bruto desabilitado |
| Merge sobre branch alterada | Comparação com commit-base e pausa obrigatória |
| Testes caros ou lentos | Testes focados por task, suíte completa após integração e timeout |
| Excesso de testes gerados | Prompt e contrato exigem apenas testes essenciais da demanda |
| Acoplamento a LangGraph ou fornecedor | Portas próprias e dependência restrita aos adapters |

## 25. Referências

- [DORA — State of AI-assisted Software Development 2025](https://dora.dev/research/2025/dora-report/)
- [DORA — ROI of AI-assisted Software Development 2026](https://cloud.google.com/resources/content/dora-roi-of-ai-assisted-software-development)
- [AWS — AI-Driven Development Life Cycle](https://aws.amazon.com/blogs/devops/ai-driven-development-life-cycle/)
- [LangGraph — Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph — Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)
- [Claude Agent SDK for Python](https://code.claude.com/docs/en/agent-sdk/python)
- [Pi RPC](https://pi.dev/docs/latest/rpc)
- [Anthropic Sandbox Runtime](https://github.com/anthropics/sandbox-runtime)
- [Typer](https://typer.tiangolo.com/)
- [prompt_toolkit](https://python-prompt-toolkit.readthedocs.io/)
- [Rich](https://rich.readthedocs.io/)
