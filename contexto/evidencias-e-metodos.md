# Evidências e métodos para desenvolvimento assistido

## Objetivo

Este documento complementa o contexto do Super Simple Software Factory (SSSF)
com três referências que respondem a perguntas diferentes:

1. DORA 2025: o que a evidência indica sobre adoção e desempenho.
2. DORA ROI 2026: como construir uma linha de base e medir retorno.
3. AWS AI-DLC: como organizar um método com decisões humanas explícitas.

O objetivo não é tratar os documentos como equivalentes. DORA oferece pesquisa
e instrumentos de medição; AI-DLC oferece uma definição de método; SSSF oferece
uma implementação de orquestração determinística que pode incorporar práticas
dos dois.

## Estado das fontes

Os caminhos locais mencionados na solicitação não estavam presentes no
workspace durante esta revisão:

- `fontes/dora/2025-state-of-ai-assisted-software-development-full-en.pdf`;
- `fontes/dora/2026-roi-of-ai-assisted-software-development.pdf`;
- `fontes/aws/ai-driven-development-lifecycle-whitepaper.pdf`.

A análise foi feita com os PDFs oficiais publicados por Google Cloud/DORA e AWS.
Os caminhos acima permanecem registrados como os locais esperados caso as
cópias sejam adicionadas ao repositório posteriormente.

## Ordem de leitura recomendada

| Ordem | Documento | Pergunta que responde |
| --- | --- | --- |
| 1 | DORA 2025 | Quais resultados estão associados à adoção e quais capacidades influenciam esses resultados? |
| 2 | DORA AI Capabilities Model | O que uma organização pode implementar e acompanhar na prática? |
| 3 | DORA ROI 2026 | Como medir custo, adaptação, instabilidade e retorno? |
| 4 | AWS AI-DLC | Como estruturar planejamento, execução e verificação humana? |
| 5 | SSSF | Como codificar o fluxo, os contratos, gates e rastros operacionais? |

## 1. DORA 2025: melhor ponto de partida

O [State of AI-assisted Software Development 2025](https://dora.dev/research/2025/dora-report/)
é o ponto de partida mais equilibrado deste conjunto para discutir adoção,
produtividade, qualidade e desempenho organizacional.

### Principais conclusões

O resumo executivo apresenta a tecnologia como um amplificador do sistema
organizacional existente. Ela amplia tanto capacidades maduras quanto problemas
já presentes. Ganhos locais de velocidade podem desaparecer em gargalos de
teste, revisão, aprovação e entrega.

Dados destacados pelo relatório:

- quase 5.000 profissionais responderam ao survey;
- mais de 100 horas de dados qualitativos complementaram o survey;
- 90% dos respondentes declararam usar a tecnologia no trabalho;
- mais de 80% perceberam aumento de produtividade;
- 30% declararam pouca ou nenhuma confiança no código produzido;
- a adoção apareceu associada a maior throughput de entrega;
- a adoção também apareceu associada a maior instabilidade de entrega;
- plataforma interna, dados, fluxo de valor e disciplina de engenharia
  condicionam a conversão de velocidade local em desempenho organizacional.

As páginas 3 a 6 da versão `v. 2025.2` contêm o resumo executivo. A página 7
é uma apresentação da comunidade DORA, não uma continuação dos achados.

### Como ler a evidência

A ressalva metodológica é essencial. A maior parte das variáveis vem de survey
e auto-relato. A coleta global ocorreu entre 13 de junho e 21 de julho de 2025.
O desenho combina:

- recrutamento orgânico por canais da comunidade;
- painel para complementar grupos, setores e organizações sub-representados;
- distribuição aleatória de participantes entre quatro fluxos temáticos;
- entrevistas semiestruturadas com 78 profissionais;
- triangulação entre dados qualitativos e quantitativos;
- teoria causal expressa como grafo acíclico direcionado, ou DAG;
- análise fatorial confirmatória para validar construtos do survey;
- modelagem de equações estruturais para testar a estrutura proposta;
- modelos bayesianos, intervalos de credibilidade e diagnósticos preditivos.

Os autores deixam explícitas suas hipóteses causais, mas reconhecem os limites
de dados observacionais e descrevem os resultados finais como comparações
fundamentadas. Portanto:

- associação consistente não equivale a causalidade demonstrada;
- medidas de percepção não substituem telemetria operacional;
- nem todos os respondentes receberam todas as perguntas temáticas;
- os resultados devem formar hipóteses para experimentos no contexto local;
- comparar o mesmo serviço ao longo do tempo é mais útil do que criar rankings
  entre equipes com contextos diferentes.

As páginas 113 a 130 documentam seleção de perguntas, coleta, entrevistas e o
workflow estatístico. A [errata oficial](https://dora.dev/research/2025/errata/)
indica que `v. 2025.2` é a versão digital corrigida disponível na revisão.

### Consequência para o SSSF

Não se deve avaliar o SSSF apenas por quantidade de código ou rapidez de uma
fase. O objeto de avaliação é o sistema completo:

```text
pedido -> planejamento -> implementação -> verificação -> revisão
       -> integração -> entrega -> resultado para o usuário
```

O trace do SSSF cobre bem as fases internas, envelopes, gates, processos e
consumo. Para medir desempenho sistêmico, ele precisa ser combinado com dados de
pull requests, CI, deploy, incidentes, retrabalho e resultado de produto.

## 2. DORA AI Capabilities Model: guia de implementação

O [DORA AI Capabilities Model](https://dora.dev/ai/capabilities-model/report/)
é o complemento prático do relatório de 2025. Sua versão `v. 2025.1` detalha
estratégias, táticas iniciais e formas de acompanhar sete capacidades.

### As sete capacidades e sua aplicação ao SSSF

| Capacidade DORA | Aplicação no SSSF | Sinal de acompanhamento |
| --- | --- | --- |
| Posição clara e comunicada | Definir ferramentas permitidas, dados proibidos, autoridade para escrita, commits, deploy e aprovação. | Percentual da equipe que entende a política e sabe aplicá-la. |
| Ecossistema de dados saudável | Manter documentação, metadados e fontes internas com donos, atualização e validação. | Tempo para obter um dado, frescor, completude e incidentes causados por dados ruins. |
| Dados internos acessíveis | Fornecer apenas o contexto relevante por arquivos versionados, busca, RAG ou MCP, com controle de acesso. | Precisão da recuperação, citações válidas e tempo para localizar contexto. |
| Controle de versão forte | Versionar código, configuração, prompts, testes e infraestrutura; favorecer alterações atômicas e reversíveis. | Tamanho e frequência de commits, conflitos, branches ativas e tempo de rollback. |
| Trabalho em lotes pequenos | Dividir o pedido em fatias independentes, testáveis e entregáveis em horas ou poucos dias. | Lead time, tamanho de mudança, WIP e frequência de integração. |
| Foco no usuário | Incluir necessidade, resultado esperado e métrica de produto no pedido e nos gates de aceitação. | Adoção, retenção, satisfação e presença do objetivo do usuário nos artefatos. |
| Plataforma interna de qualidade | Oferecer caminhos seguros e de autosserviço para build, teste, segurança, deploy, observabilidade e rollback. | Satisfação, adoção, retenção, sucesso da tarefa e métricas DORA. |

### Prioridades práticas

Para uma adoção do SSSF, a ordem mais segura é:

1. Escrever a política de uso e os limites de autoridade.
2. Configurar testes e gates reais antes de ampliar o escopo.
3. Versionar prompts, configurações e artefatos operacionais reproduzíveis.
4. Conectar somente fontes internas com qualidade, dono e acesso definidos.
5. Limitar tamanho de lote, WIP e duração das branches.
6. Vincular toda entrega a um resultado observável para o usuário.
7. Transformar as partes repetidas em capacidades da plataforma interna.

O modelo também alerta contra fornecer documentos extensos e indiscriminados ao
modelo. Contexto preciso, atualizado e recuperado para a tarefa é preferível a
inserir todo o acervo em uma janela de contexto.

## 3. DORA ROI 2026: melhor documento para medição

O [ROI of AI-assisted Software Development](https://cloud.google.com/resources/content/dora-roi-of-ai-assisted-software-development)
conecta desempenho de engenharia e resultado financeiro. A versão revisada foi
`v. 2026.1`.

### Produtividade como resultado sistêmico

O documento não reduz produtividade à quantidade de código. O retorno pode ser
afetado por eficiência de custos, segurança, experiência de desenvolvimento,
experiência do usuário, verificação, adaptação do pipeline e instabilidade.

A medição exige uma linha de base anterior. Sem o "antes", não é possível
separar mudança real de variação normal do sistema.

### Curva J

A página 4 introduz a curva J de realização de valor. Depois da adoção, a
organização pode experimentar uma queda temporária de produtividade e maior
instabilidade antes de superar a linha de base.

As páginas 9 e 10 detalham três causas:

1. Curva de aprendizado: tempo dedicado a aprender interfaces, fluxos, contexto,
   intenção e especificação.
2. `Verification tax`: tempo humano gasto revisando uma quantidade maior de
   saídas e verificando segurança, arquitetura e confiabilidade.
3. Adaptação do pipeline: testes, revisões e aprovações precisam acompanhar o
   aumento de volume e expõem restrições legadas.

A curva J é um modelo conceitual, não uma previsão de prazo. O relatório afirma
que profundidade e duração da queda são imprevisíveis e dependem de maturidade
técnica, aprendizado contínuo e saúde da plataforma interna.

### Como medir o SSSF

| Dimensão | Medida inicial | Fonte sugerida |
| --- | --- | --- |
| Fluxo | Lead time para mudanças e frequência de deploy | Git, CI/CD e sistema de deploy |
| Estabilidade | Taxa de falha de mudança e tempo de recuperação | Deploys e incidentes |
| Retrabalho | Taxa de retrabalho e reversões | Git, pull requests e incidentes |
| Verification tax | Horas humanas de revisão, correções por gate e ciclos até aprovação | Trace SSSF e plataforma de revisão |
| Custo | Provedor, tokens, computação, revisão humana e manutenção do pipeline | SQLite SSSF, billing e apontamento amostral |
| Qualidade | Defeitos escapados, vulnerabilidades, falhas de teste e regressões | CI, segurança e incidentes |
| Produto | Adoção, retenção, satisfação ou outra métrica ligada ao pedido | Telemetria de produto |
| Experiência | Fricção percebida, satisfação e burnout | Survey periódico curto |

### Desenho mínimo de um piloto

1. Escolher um serviço ou tipo de trabalho recorrente.
2. Coletar uma linha de base por um período suficiente para capturar variação.
3. Registrar volume, complexidade e tipo de trabalho para evitar comparações falsas.
4. Introduzir o SSSF em uma parte delimitada do fluxo.
5. Medir custo total, verification tax, fluxo, estabilidade, qualidade e produto.
6. Comparar o mesmo serviço ao longo do tempo e, quando possível, usar um grupo de comparação adequado.
7. Tratar a queda inicial como hipótese observável, não como justificativa automática para qualquer resultado ruim.
8. Decidir expansão com base em capacidade recuperada e resultado, não em linhas de código.

## 4. AWS AI-DLC: melhor definição explícita de método

O [AI-Driven Development Life Cycle](https://aws.amazon.com/blogs/devops/ai-driven-development-life-cycle/)
foi apresentado pela AWS em 31 de julho de 2025. O
[Method Definition Paper](https://prod.d13rzhkk8cj2z0.amplifyapp.com/aidlc.pdf)
descreve seus princípios, artefatos, fases e rituais.

### Ciclo recorrente

O modelo mental pode ser resumido assim:

> IA cria o plano → solicita esclarecimentos → humanos decidem → IA executa → humanos verificam.

Esse ciclo se repete em cada atividade. A automação propõe e executa; pessoas
mantêm autoridade sobre contexto, escolhas críticas, trade-offs e aceitação.

### Três fases

| Fase | Finalidade | Supervisão humana |
| --- | --- | --- |
| Inception | Converter intenção de negócio em requisitos, histórias, riscos, critérios e unidades de trabalho. | Esclarecer, ajustar escopo e validar alinhamento com o negócio. |
| Construction | Produzir design de domínio, design lógico, código e testes para unidades aprovadas. | Decidir arquitetura e trade-offs, revisar artefatos e validar testes. |
| Operations | Tratar infraestrutura, deploy, observabilidade, manutenção e resposta operacional. | Aprovar ações e verificar aderência a SLA, risco e conformidade. |

As páginas 2 a 5 do whitepaper concentram princípios, artefatos, as três fases,
o workflow recursivo e um exemplo greenfield.

### Artefatos e unidades de trabalho

- `Intent`: declaração de alto nível do resultado desejado.
- `Unit`: parte coesa e implantável que entrega valor mensurável.
- `Bolt`: menor iteração, medida em horas ou dias.
- Artefatos persistidos: requisitos, histórias, riscos, modelos, decisões,
  testes e planos formam memória de contexto versionada.
- Rastreabilidade: artefatos devem se conectar para permitir navegação do
  objetivo até código, teste e operação, e no sentido inverso.

### Adaptabilidade

AI-DLC evita impor o mesmo fluxo e a mesma profundidade a todo tipo de mudança.
Uma correção simples pode pular etapas; uma funcionalidade complexa pode exigir
requisitos, arquitetura, riscos e testes detalhados. Pessoas validam tanto a
largura quanto a profundidade propostas para o workflow.

Em [29 de novembro de 2025](https://aws.amazon.com/blogs/devops/open-sourcing-adaptive-workflows-for-ai-driven-development-life-cycle-ai-dlc/),
a AWS publicou a implementação adaptativa como open source. O repositório atual é
[awslabs/aidlc-workflows](https://github.com/awslabs/aidlc-workflows).

### Limite da evidência

O método é conceitualmente forte e oferece um ciclo aplicável de proposta,
decisão, execução e verificação. Porém, afirmações de velocidade e qualidade
publicadas pelos autores e pelo fornecedor não constituem, sozinhas, evidência
comparativa independente.

Use AI-DLC como definição de método e fonte de hipóteses. Use linha de base,
telemetria operacional e resultados de produto para avaliar seu efeito local.

## 5. Como as referências se complementam

| Referência | Contribuição principal | O que não resolve sozinha |
| --- | --- | --- |
| DORA 2025 | Evidência sobre adoção, capacidades e resultados associados. | Causalidade definitiva ou receita detalhada para um repositório específico. |
| DORA Capabilities | Táticas e sinais para melhorar sete capacidades organizacionais. | Um motor executável de workflow. |
| DORA ROI 2026 | Linha de base, curva J, verification tax e tradução financeira. | Garantia de retorno ou prazo universal para recuperá-lo. |
| AWS AI-DLC | Método adaptativo com planejamento, decisão e validação humana. | Evidência independente de ganho ou controle determinístico completo. |
| SSSF | Orquestração em Python, envelopes, gates, permissões, retries e trace. | Aprovação humana, sandbox, branch por execução e medição de produto prontos. |

## 6. Recomendações para evoluir o SSSF

### Controles prioritários

1. Substituir imediatamente os comandos de qualidade de exemplo por comandos reais.
2. Adicionar gates humanos explícitos antes de mudanças irreversíveis, merge e deploy.
3. Executar cada mudança em branch e ambiente isolados.
4. Exigir lotes pequenos, diffs revisáveis e possibilidade de rollback.
5. Registrar no envelope o objetivo do usuário e a medida de sucesso.
6. Versionar prompts, configurações, gates e fontes de contexto.
7. Conectar o trace a CI/CD, revisão, deploy, incidentes e telemetria de produto.
8. Medir tempo humano de verificação como custo de primeira classe.

### Novo fluxo de aceitação sugerido

```text
Intenção
  -> plano proposto
  -> esclarecimentos
  -> aprovação humana do plano
  -> implementação em lote pequeno
  -> testes e gates determinísticos
  -> revisão especializada
  -> verificação humana do resultado
  -> merge/deploy controlado
  -> medição do resultado de produto
```

Esse fluxo combina a autoridade humana explícita do AI-DLC, a disciplina
sistêmica recomendada pela DORA e o plano de controle determinístico do SSSF.

## Fontes oficiais

- [DORA 2025 - página oficial](https://dora.dev/research/2025/dora-report/)
- [DORA 2025 - PDF completo](https://services.google.com/fh/files/misc/2025_state_of_ai_assisted_software_development.pdf)
- [DORA 2025 - errata](https://dora.dev/research/2025/errata/)
- [DORA AI Capabilities Model - página oficial](https://dora.dev/ai/capabilities-model/report/)
- [DORA AI Capabilities Model - PDF](https://services.google.com/fh/files/misc/2025_dora_ai_capabilities_model.pdf)
- [DORA ROI 2026 - página oficial](https://cloud.google.com/resources/content/dora-roi-of-ai-assisted-software-development)
- [DORA ROI 2026 - PDF](https://services.google.com/fh/files/misc/dora-roi-of-ai-assisted-software-development-2026.pdf)
- [AWS AI-DLC - publicação original](https://aws.amazon.com/blogs/devops/ai-driven-development-life-cycle/)
- [AWS AI-DLC - Method Definition Paper](https://prod.d13rzhkk8cj2z0.amplifyapp.com/aidlc.pdf)
- [AWS - publicação dos workflows adaptativos](https://aws.amazon.com/blogs/devops/open-sourcing-adaptive-workflows-for-ai-driven-development-life-cycle-ai-dlc/)
- [AWS - repositório dos workflows](https://github.com/awslabs/aidlc-workflows)
