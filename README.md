# CH8 Hub Build AI

Plataforma de ingestão de documentos e geração automática de painéis de inteligência para integrações corporativas. Processa documentos, código-fonte, imagens e repositórios com IA (Bedrock Claude) e gera análise técnica completa, code review, diagramas C1-C4 e planos de contingência.

---

## Sumário

- [Visão Geral](#visão-geral)
- [Arquitetura](#arquitetura)
- [Pipeline de Processamento](#pipeline-de-processamento)
- [Tecnologias](#tecnologias)
- [Instalação](#instalação)
- [Configuração AWS](#configuração-aws)
- [Como Usar](#como-usar)
- [Endpoints da API](#endpoints-da-api)
- [Integração com o Hub](#integração-com-o-hub)
- [Segurança](#segurança)

---

## Visão Geral

O **CH8 Hub Build AI** é a ferramenta de *onboarding* que alimenta o ValeNet Hub (porta 8900). Enquanto o Hub exibe os painéis de inteligência já processados, o Builder é onde você cria novos projetos, faz upload dos artefatos brutos e dispara o pipeline de IA para gerar a análise.

**Fluxo resumido:**

```
Upload de artefatos
  ↓
Pipeline IA (5 etapas via Bedrock)
  ↓
JSON de análise + graph_node
  ↓
Exportar para o ValeNet Hub
```

---

## Arquitetura

```
C:\projetos\hatpkg\builder\
├── server.py          # Backend FastAPI (porta 8901)
├── static/
│   └── index.html     # SPA — interface completa (login, dashboard, projetos)
├── projects/          # Dados persistidos dos projetos
│   └── <uuid>/
│       ├── project.json   # Metadados + resultado do pipeline
│       └── files/         # Arquivos enviados pelo usuário
├── requirements.txt
└── README.md
```

### Componentes Principais

| Componente | Descrição |
|---|---|
| `server.py` | FastAPI, orquestra o pipeline, serve a SPA, gerencia auth |
| `static/index.html` | SPA com sidebar, 9 abas por projeto, dashboard KPIs |
| `projects/` | Banco de dados local em JSON por projeto |
| AWS Bedrock | Motor de IA — modelo `us.anthropic.claude-sonnet-4-6` |

### Modelo de Dados — `project.json`

```json
{
  "id": "<uuid>",
  "name": "Nome do Projeto",
  "description": "...",
  "prompt": "contexto adicional",
  "status": "done | error | processing | draft",
  "repos": [{"label":"...", "url":"...", "token":"..."}],
  "meta": { "global_id": "...", "familia": "...", "sistemas": [], "router_var": "..." },
  "analysis": { "resumo": "...", "fluxo": [], "riscos": [], "chave_roteamento": {}, ... },
  "contingency": { "branch_name": "...", "RoutingBean": "...", "camel_snippet": "...", ... },
  "code_review": { "qualidade": 8.5, "resumo": "...", "seguranca": [], ... },
  "diagrams": { "c1": {"mermaid":"..."}, "c2": {...}, "c3": {...}, "c4": {...} }
}
```

---

## Pipeline de Processamento

O pipeline executa 5 etapas em sequência via AWS Bedrock:

### Etapa 1 — Extract
Extrai metadados estruturados: `global_id`, família, sistemas envolvidos, variável de roteamento, operação, sincronicidade.

### Etapa 2 — Analysis
Análise técnica completa: fluxo de dados, chave de roteamento (campo + certeza), riscos com severidade, performance, monitoramento (métricas, alertas, MDC), campos de entrada/saída, mapeamentos.

### Etapa 3 — Contingency
Gera 3 artefatos separados para evitar erros de JSON:
1. **Plano** — branch name, instruções passo a passo, configmap YAML
2. **RoutingBean.java** — código Java completo para roteamento
3. **Camel snippet** — trecho de rota Apache Camel com `.choice()`

### Etapa 4 — Code Review
Análise profunda do código-fonte encontrado nos arquivos e repositórios:
- Score de qualidade `/10`
- Riscos de segurança (OWASP / CWE)
- Bugs potenciais com probabilidade e localização
- Análise de performance e escalabilidade
- Padrões arquiteturais identificados
- Sugestões de melhoria priorizadas
- Insights de IA

### Etapa 5 — Diagrams
Gera 4 diagramas Mermaid independentes:
- **C1** — Contexto do Sistema (`graph LR`)
- **C2** — Containers / Serviços (`graph TD`)
- **C3** — Componentes Internos (`graph TD`)
- **C4** — Fluxo de Execução (`sequenceDiagram`)

---

## Tecnologias

| Tecnologia | Versão | Uso |
|---|---|---|
| Python | 3.10+ | Runtime |
| FastAPI | 0.111.0 | API REST + servidor estático |
| Uvicorn | 0.29.0 | ASGI server |
| AWS Boto3 | 1.34.82 | Acesso ao Bedrock |
| Claude Sonnet 4.6 | Hatpkg TAquino 1.1 | Motor de IA |
| PyMuPDF | 1.24.3 | Leitura de PDF |
| python-docx | 1.1.2 | Leitura de DOCX |
| Mermaid.js | 10.x | Renderização dos diagramas |

---

## Instalação

### Pré-requisitos

- Python 3.10+
- AWS CLI configurado com permissão para `bedrock:InvokeModel`
- Região AWS: `us-east-1`

### Passos

```bash
# 1. Entrar na pasta
cd C:\projetos\hatpkg\builder

# 2. Criar ambiente virtual (opcional mas recomendado)
python -m venv .venv
.venv\Scripts\activate

# 3. Instalar dependências
pip install -r requirements.txt

# 4. Iniciar o servidor
python server.py
```

O servidor estará disponível em: **http://localhost:8901**

---

## Configuração AWS

O Builder usa AWS Bedrock via `boto3`. Configure as credenciais usando um dos métodos:

**Opção 1 — AWS CLI**
```bash
aws configure
# AWS Access Key ID: <sua-key>
# AWS Secret Access Key: <seu-secret>
# Default region name: us-east-1
```

**Opção 2 — Variáveis de ambiente**
```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1
```

**Permissões IAM necessárias:**
```json
{
  "Effect": "Allow",
  "Action": ["bedrock:InvokeModel"],
  "Resource": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-*"
}
```

---

## Como Usar

### 1. Login

Acesse **http://localhost:8901** e faça login:
- **Usuário:** `hatkg`
- **Senha:** (definida no `server.py`)

Após o login você é direcionado ao **Dashboard** com KPIs de todos os projetos.

### 2. Criar Projeto

Clique em **＋ Novo Projeto** na sidebar e preencha:
- **Nome** — identificador legível
- **Descrição** — contexto da integração
- **Prompt adicional** — instruções extras para a IA
- **Repositório** — URL + token (pode adicionar mais depois)

### 3. Adicionar Arquivos

Na aba **📁 Arquivos**:
- Arraste ou clique para fazer upload
- Tipos suportados: PDF, DOCX, TXT, Java, XML, XSLT, XSD, WSDL, YAML, PNG, JPG, CSV, XLSX

### 4. Adicionar Repositórios

Na aba **⚙️ Configurar → Repositórios**:
- Adicione múltiplos repositórios com label, URL e token
- O conteúdo é lido pelo Bedrock como contexto adicional

### 5. Processar

Na aba **🤖 Processar**:
- Clique em **Iniciar Pipeline Completo**
- Acompanhe o progresso das 5 etapas em tempo real
- Em caso de erro, você pode reprocessar etapas individuais

### 6. Visualizar Resultados

Após o processamento:
- **📊 Resultado** — metadados, análise técnica, riscos, chave de roteamento
- **🔀 Branch & Código** — RoutingBean.java, Camel snippet, configmap, instruções
- **🔍 Code Review** — score, segurança OWASP, bugs, performance, arquitetura
- **📐 Diagramas C1-C4** — diagramas Mermaid renderizados interativamente
- **💬 Chat IA** — converse sobre a integração com a IA especialista

### 7. Exportar

Na aba **📤 Exportar**:
- Gera `graph_node` e `analysis_file` no formato do ValeNet Hub
- Copie e cole nos arquivos `data/graph.json` e `data/analysis/` do Hub

---

## Endpoints da API

### Autenticação
| Método | Endpoint | Descrição |
|---|---|---|
| POST | `/api/login` | Login (`{username, password}`) |
| POST | `/api/logout` | Logout (limpa cookie) |
| GET | `/api/me` | Retorna usuário autenticado |

### Dashboard
| Método | Endpoint | Descrição |
|---|---|---|
| GET | `/api/dashboard` | KPIs agregados de todos os projetos |

### Projetos
| Método | Endpoint | Descrição |
|---|---|---|
| GET | `/api/projects` | Lista todos os projetos |
| POST | `/api/projects` | Cria novo projeto |
| GET | `/api/projects/{pid}` | Retorna projeto completo |
| PUT | `/api/projects/{pid}` | Atualiza metadados |
| DELETE | `/api/projects/{pid}` | Remove projeto |

### Arquivos
| Método | Endpoint | Descrição |
|---|---|---|
| GET | `/api/projects/{pid}/files` | Lista arquivos do projeto |
| POST | `/api/projects/{pid}/files` | Upload de arquivo |
| DELETE | `/api/projects/{pid}/files/{fname}` | Remove arquivo |

### Repositórios
| Método | Endpoint | Descrição |
|---|---|---|
| GET | `/api/projects/{pid}/repos` | Lista repositórios |
| POST | `/api/projects/{pid}/repos` | Adiciona repositório |
| DELETE | `/api/projects/{pid}/repos/{idx}` | Remove repositório |

### Pipeline
| Método | Endpoint | Descrição |
|---|---|---|
| POST | `/api/projects/{pid}/process` | Inicia pipeline completo |
| GET | `/api/projects/{pid}/status` | Status atual do pipeline |
| POST | `/api/projects/{pid}/rerun/{step}` | Reprocessa etapa específica |
| GET | `/api/projects/{pid}/export` | Gera JSON para o Hub |

### Chat
| Método | Endpoint | Descrição |
|---|---|---|
| POST | `/api/projects/{pid}/chat` | Envia mensagem ao assistente IA |

---

## Integração com o Hub

Após exportar, copie os arquivos gerados para o ValeNet Hub (porta 8900):

```bash
# 1. Adicione o graph_node em data/graph.json
# 2. Salve o analysis_file em:
data/analysis/analysis_<global_id>.json
```

O Hub detecta automaticamente os novos arquivos na próxima recarga.

---

## Segurança

- Autenticação via cookie `ch8_session` (httponly, samesite=lax)
- Token de sessão derivado de UUID v5 (determinístico, não adivinhável)
- Credenciais de repositório armazenadas localmente (nunca enviadas a terceiros)
- Todo processamento ocorre via AWS Bedrock (dados não saem da conta AWS)
- Arquivos enviados ficam restritos ao diretório `projects/` local

---

*CH8 Hub Build AI · Hatpkg TAquino 1.1 · porta 8901*
