"""
CH8 Hub Build AI — Backend de ingestão e geração de painéis de integração.
Porta 8901.
"""
import sys, os, json, time, re, uuid, shutil, base64
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List
import boto3

HERE     = os.path.dirname(os.path.abspath(__file__))
PROJECTS = os.path.join(HERE, 'projects')
os.makedirs(PROJECTS, exist_ok=True)

bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')
MODEL   = 'us.anthropic.claude-sonnet-4-6'

app = FastAPI(title='CH8 Hub Build AI', version='1.0.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

# ── Bedrock helpers ───────────────────────────────────────────────────────────

def _clean_json(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'^```\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    return raw.strip()

def _repair_json(text: str):
    """Fecha estruturas JSON abertas para reparar respostas truncadas."""
    text = text.strip()
    for length in [len(text), len(text) - 50, len(text) - 200, len(text) - 500]:
        if length <= 10:
            break
        chunk = text[:length].rstrip().rstrip(',')
        depth_brace = 0
        depth_bracket = 0
        in_str = False
        esc_next = False
        for c in chunk:
            if esc_next: esc_next = False; continue
            if c == '\\' and in_str: esc_next = True; continue
            if c == '"': in_str = not in_str; continue
            if in_str: continue
            if c == '{':   depth_brace   += 1
            elif c == '}': depth_brace    = max(0, depth_brace - 1)
            elif c == '[': depth_bracket  += 1
            elif c == ']': depth_bracket  = max(0, depth_bracket - 1)
        attempt = chunk
        if in_str: attempt += '"'
        attempt += ']' * depth_bracket + '}' * depth_brace
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue
    return None

def _call_bedrock(messages: list, max_tokens: int = 4096) -> str:
    body = json.dumps({
        'anthropic_version': 'bedrock-2023-05-31',
        'max_tokens': max_tokens,
        'messages': messages,
    })
    resp = bedrock.invoke_model(modelId=MODEL, body=body)
    return json.loads(resp['body'].read())['content'][0]['text']

def _safe_call_json(messages: list, max_tokens: int = 4096) -> dict:
    """Chama Bedrock e tenta reparar o JSON em múltiplas estratégias."""
    raw = _call_bedrock(messages, max_tokens)
    cleaned = _clean_json(raw)

    for candidate in [cleaned, raw]:
        # 1) parse direto
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        # 2) extrair bloco JSON
        m = re.search(r'\{[\s\S]*\}', candidate, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        # 3) reparar truncado
        result = _repair_json(candidate)
        if result is not None:
            return result

    raise ValueError(f'JSON inválido. Primeiros 400 chars: {raw[:400]}')

def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^```[\w]*\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    return text.strip()

# ── extração de arquivos ──────────────────────────────────────────────────────

def extract_text(path: str, filename: str) -> str:
    ext = filename.rsplit('.', 1)[-1].lower()
    try:
        if ext in ('txt','md','yaml','yml','xml','java','py','js','ts',
                   'json','xslt','xsd','wsdl','sh','properties','groovy'):
            with open(path, encoding='utf-8', errors='replace') as f:
                return f.read()[:20000]
        if ext == 'docx':
            from docx import Document
            doc = Document(path)
            return '\n'.join(p.text for p in doc.paragraphs)[:20000]
        if ext == 'pdf':
            try:
                import fitz
                doc = fitz.open(path)
                text = '\n'.join(page.get_text() for page in doc)
                doc.close()
                return text[:20000]
            except ImportError:
                return f'[PDF: {filename} — instale PyMuPDF para extração]'
        return f'[Binário: {filename}]'
    except Exception as e:
        return f'[Erro ao ler {filename}: {e}]'

def extract_image_b64(path: str) -> tuple:
    ext = path.rsplit('.', 1)[-1].lower()
    mt  = {'png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg',
           'gif':'image/gif','webp':'image/webp'}.get(ext, 'image/png')
    with open(path, 'rb') as f:
        return mt, base64.standard_b64encode(f.read()).decode()

# ── helpers de projeto ────────────────────────────────────────────────────────

def load_project(pid: str) -> dict:
    path = os.path.join(PROJECTS, pid, 'project.json')
    if not os.path.exists(path):
        raise HTTPException(404, f'Projeto {pid} não encontrado')
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def save_project(pid: str, data: dict):
    folder = os.path.join(PROJECTS, pid)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'project.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def set_status(pid: str, step: str, pct: int, msg: str):
    try:
        p = load_project(pid)
        p['processing'] = {'step': step, 'pct': pct, 'msg': msg, 'ts': time.strftime('%H:%M:%S')}
        save_project(pid, p)
    except Exception:
        pass

# ── builder de contexto ───────────────────────────────────────────────────────

def _build_context(pid: str, proj: dict) -> list:
    parts, images = [], []

    if proj.get('description'): parts.append(f'CONTEXTO DO PROJETO:\n{proj["description"]}')
    if proj.get('prompt'):      parts.append(f'INSTRUÇÕES E CONTEXTO ADICIONAL DO USUÁRIO:\n{proj["prompt"]}')

    # múltiplos repositórios
    for i, repo in enumerate(proj.get('repos', []), 1):
        url = repo.get('url', '')
        lbl = repo.get('label', '')
        if url: parts.append(f'REPOSITÓRIO {i}{f" ({lbl})" if lbl else ""}: {url}')
    # retrocompatibilidade
    if proj.get('repo_url'):
        if not any(r.get('url') == proj['repo_url'] for r in proj.get('repos', [])):
            parts.append(f'REPOSITÓRIO: {proj["repo_url"]}')

    ff = os.path.join(PROJECTS, pid, 'files')
    if os.path.exists(ff):
        for fn in sorted(os.listdir(ff)):
            fp  = os.path.join(ff, fn)
            ext = fn.rsplit('.', 1)[-1].lower()
            if ext in ('png','jpg','jpeg','gif','webp'):
                images.append((fp, fn))
            else:
                t = extract_text(fp, fn)
                if t: parts.append(f'\n=== ARQUIVO: {fn} ===\n{t}')

    # instrução de contextualização multi-documento
    if len(parts) > 2:
        parts.insert(0, 'INSTRUÇÃO: Analise TODO o conteúdo abaixo em conjunto. Há múltiplos documentos, códigos e informações. Contextualize e consolide tudo para extrair informações precisas sobre a integração.')

    content = []
    if parts:
        content.append({'type':'text','text':'\n\n'.join(parts)[:40000]})
    for fp, fn in images[:5]:
        mt, b64 = extract_image_b64(fp)
        content.append({'type':'image','source':{'type':'base64','media_type':mt,'data':b64}})
        content.append({'type':'text','text':f'[Imagem acima: {fn}]'})
    return content

# ── passos do pipeline ────────────────────────────────────────────────────────

def _step_extract(pid: str, proj: dict) -> dict:
    set_status(pid, 'extract', 8, 'Lendo e indexando todos os documentos...')
    ctx = _build_context(pid, proj)
    ctx.append({'type':'text','text':"""
Analise TODO o conteúdo acima e extraia os metadados desta integração.
Retorne APENAS JSON válido, sem markdown, sem texto extra:
{
  "global_id": "ex: cm20080014-10p-valenet",
  "familia_id": "ex: cm20080014",
  "nome": "Nome legível",
  "sistemas_origem": ["Sistema A"],
  "sistemas_destino": ["SAP ECC"],
  "operacao": "O que faz",
  "sincronicidade": "sincrona ou assincrona",
  "protocolo": ["AMQP"],
  "padrao_eip": ["message-translator"],
  "descricao": "2 linhas técnicas",
  "dominio": "ex: Portos",
  "tags": ["tag1"],
  "router_var": "campo de roteamento",
  "fila_sap_pi": "fila se mencionada",
  "legs": 2
}"""})
    set_status(pid, 'extract', 18, 'Extraindo metadados com IA...')
    return _safe_call_json([{'role':'user','content':ctx}], 2000)


def _step_analysis(pid: str, proj: dict, meta: dict) -> dict:
    set_status(pid, 'analysis', 28, 'Iniciando análise técnica profunda...')
    ctx = _build_context(pid, proj)
    ctx.append({'type':'text','text':f"""
Integração: {meta.get("global_id","?")} | Operação: {meta.get("operacao","")}
Sistemas: {meta.get("sistemas_origem",[])} → {meta.get("sistemas_destino",[])}
Campo roteamento: {meta.get("router_var","")}

Faça análise técnica completa. APENAS JSON sem markdown:
{{
  "resumo": "Resumo executivo em 1-2 frases",
  "fluxo": ["passo 1", "passo 2", "passo 3", "passo 4"],
  "chave_roteamento": {{
    "campo": "nome do campo",
    "compostos": ["outros campos"],
    "onde": "onde está no payload/código",
    "certeza": "alta|media|baixa"
  }},
  "riscos": [
    {{"sev": "alta|media|baixa", "desc": "descrição", "fix": "como mitigar"}}
  ],
  "performance": [
    {{"tipo": "gargalo|otimizacao", "desc": "descrição", "fix": "recomendação"}}
  ],
  "monitoramento": {{
    "metricas": ["msgs/min", "taxa de erro", "latencia ms"],
    "alertas": ["latencia>5s", "erro>0"],
    "ehl": "MDC: globalId=X, plant, destination"
  }},
  "campos_entrada": [
    {{"nome": "campo", "tipo": "string", "obrigatorio": true}}
  ],
  "campos_saida": [],
  "mapeamentos": ["transformação 1", "transformação 2"],
  "tecnologias": ["Apache Camel", "ActiveMQ", "Spring Boot"]
}}"""})
    set_status(pid, 'analysis', 48, 'Processando análise com Claude...')
    return _safe_call_json([{'role':'user','content':ctx}], 3500)


def _step_contingency(pid: str, proj: dict, meta: dict, analysis: dict) -> dict:
    gid   = meta.get('global_id', 'integration')
    field = analysis.get('chave_roteamento', {}).get('campo', 'plant')

    # ─ Chamada 1: plano JSON (SEM código embutido) ───────────────────────────
    set_status(pid, 'contingency', 55, 'Planejando branch de contingência...')
    plan = _safe_call_json([{'role':'user','content': f"""
Integração: {gid} | Campo de roteamento: {field}
Destinos: SAP ECC (legado) e SAP S/4HANA (novo)

Gere plano de contingência. APENAS JSON, SEM código Java ou YAML dentro:
{{
  "branch_name": "contingencia-{gid}",
  "instrucoes": [
    "1. git checkout -b contingencia-{gid}",
    "2. Criar RoutingBean.java em src/main/java/br/com/vale/fis/bean/",
    "3. Adicionar .bean(routingBean) na rota antes da fila destino",
    "4. Atualizar configmaps DEV/QA/PRD com chaves de roteamento",
    "5. Validar campo {field} no payload de produção",
    "6. Testar em DEV antes de QA/PRD"
  ],
  "notas": "Nota mais importante sobre a implementação em 1 frase"
}}"""}], 1000)

    # ─ Chamada 2: RoutingBean.java como texto puro ────────────────────────────
    set_status(pid, 'contingency', 65, 'Gerando RoutingBean.java...')
    bean = _call_bedrock([{'role':'user','content': f"""
Escreva a classe Java RoutingBean completa para a integração {gid}.

Obrigatório:
- Pacote: br.com.vale.fis.bean
- Imports: Exchange, Logger, LoggerFactory, MDC, Map, HashMap
- Constante: private static final String GLOBAL_ID = "{gid}"
- Campo: private Map<String,String> rulesBase = new HashMap<>() com DEFAULT->ECC
- Método: public String route(Exchange ex)
  - Lê header "{field}" (com fallback para null)
  - MDC.put globalId, routingKey (valor do campo), destination
  - Retorna rulesBase.getOrDefault(campo, "ECC")
  - catch: loga erro e retorna "ECC"
  - finally: MDC.clear()
- Getter e setter para rulesBase

Retorne APENAS código Java. Sem markdown. Sem explicações."""}], 1500)
    bean = _strip_code_fences(bean)

    # ─ Chamada 3: snippet Camel como texto puro ───────────────────────────────
    set_status(pid, 'contingency', 75, 'Gerando snippet de rota Camel...')
    camel = _call_bedrock([{'role':'user','content': f"""
Escreva trecho Java DSL Apache Camel para roteamento da integração {gid}.

Deve conter:
.bean(routingBean, "route")
.choice()
  .when(header("destination").isEqualTo("S4HANA"))
    .log("[{gid}] -> S4HANA: ${{{{{field}}}}}")
    .to("{{{{routing.queue.s4hana}}}}")
  .otherwise()
    .log("[{gid}] -> ECC (default)")
    .to("{{{{routing.queue.ecc}}}}")
.end()

Retorne APENAS o trecho. Sem imports. Sem classe. Sem markdown."""}], 600)
    camel = _strip_code_fences(camel)

    plan['RoutingBean']         = bean
    plan['camel_snippet']       = camel
    plan['configmap_adicional'] = (
        f"  routing.global.id: {gid}\n"
        f"  routing.queue.ecc: queue/ECC.INBOUND\n"
        f"  routing.queue.s4hana: queue/S4HANA.INBOUND\n"
        f"  routing.field: {field}\n"
        f"  routing.default: ECC"
    )
    return plan


def _step_diagrams(pid: str, proj: dict, meta: dict, analysis: dict) -> dict:
    set_status(pid, 'diagrams', 85, 'Gerando diagramas Mermaid C1-C4...')
    gid      = meta.get('global_id', '?')
    field    = analysis.get('chave_roteamento', {}).get('campo', 'plant')
    origens  = ', '.join(meta.get('sistemas_origem', ['SistemaOrigem']))
    destinos = ', '.join(meta.get('sistemas_destino', ['SAP ECC']))
    fluxo    = analysis.get('fluxo', [])[:5]
    techs    = ', '.join(analysis.get('tecnologias', ['Apache Camel', 'ActiveMQ']))
    op       = meta.get('operacao', '')

    base = (f"Integração: {gid} | Operação: {op}\n"
            f"Origem: {origens} | Destino: {destinos}\n"
            f"Campo roteamento: {field} | Tecnologias: {techs}\n"
            f"Fluxo: {fluxo}")

    def gen(instr: str) -> str:
        raw = _call_bedrock([{'role':'user','content': base + '\n\n' + instr}], 900)
        return _strip_code_fences(raw).strip()

    set_status(pid, 'diagrams', 85, 'C1 — Contexto do Sistema...')
    c1 = gen(f"""Gere diagrama Mermaid "graph LR" C1 (Contexto) para {gid}.
Mostre: sistemas externos de origem, a integração no centro, sistemas destino (SAP ECC e SAP S4HANA).
REGRAS: IDs sem espaço (src1, fuse1, ecc1). Texto com espaço usa aspas: src1["Sistema Origem"]. Seta com rótulo: A -->|"AMQP"| B.
Retorne SOMENTE código Mermaid começando com "graph LR".""")

    set_status(pid, 'diagrams', 88, 'C2 — Containers...')
    c2 = gen(f"""Gere diagrama Mermaid "graph TD" C2 (Containers) para {gid}.
Mostre: sistema origem → fila ActiveMQ INBOUND → Red Hat Fuse/Camel → RoutingBean (campo {field}) → fila ECC e fila S4HANA.
Use subgraph para agrupar componentes relacionados.
REGRAS: IDs sem espaço. Texto com espaço usa aspas. Setas com rótulo de protocolo.
Retorne SOMENTE código Mermaid começando com "graph TD".""")

    set_status(pid, 'diagrams', 91, 'C3 — Componentes internos...')
    c3 = gen(f"""Gere diagrama Mermaid "graph TD" C3 (Componentes) para {gid}.
Mostre classes Java internas: RouteBuilder, RoutingBean, transformadores XSLT, AMQConfig, ApplicationProperties.
REGRAS: IDs = nome da classe. Texto extra usa aspas: RouteBuilder["RouteBuilder\\nconfigure()"]. Setas = dependência/chamada.
Retorne SOMENTE código Mermaid começando com "graph TD".""")

    set_status(pid, 'diagrams', 94, 'C4 — Fluxo de execução...')
    c4 = gen(f"""Gere diagrama Mermaid "sequenceDiagram" C4 (Execução) para {gid}.
Participantes: sistema origem, ActiveMQ, Apache Camel, RoutingBean, SAP ECC, SAP S4HANA.
Mostre: publicação → consumo → CBR com campo {field} → alt ECC / else S4HANA → confirmação.
REGRAS: participant Src as "Sistema Origem". Use ->> e -->>. Use alt/else para decisão de roteamento. Sem parênteses nos textos das setas.
Retorne SOMENTE código Mermaid começando com "sequenceDiagram".""")

    return {
        'c1': {'title': 'C1 — Diagrama de Contexto', 'desc': 'Visão macro: atores externos e sistemas que interagem', 'mermaid': c1},
        'c2': {'title': 'C2 — Containers', 'desc': 'Componentes técnicos, filas e protocolos de comunicação', 'mermaid': c2},
        'c3': {'title': 'C3 — Componentes Internos', 'desc': 'Classes e beans Java internos da integração', 'mermaid': c3},
        'c4': {'title': 'C4 — Fluxo de Execução', 'desc': 'Sequência detalhada de processamento de uma mensagem', 'mermaid': c4},
    }


# ── análise profunda de código ────────────────────────────────────────────────

def _step_code_review(pid: str, proj: dict, meta: dict, analysis: dict) -> dict:
    """Análise de código: arquitetura, segurança, performance, insights, análise preditiva."""
    set_status(pid, 'code_review', 63, 'Coletando arquivos de código para análise...')

    gid = meta.get('global_id', '?')

    ff = os.path.join(PROJECTS, pid, 'files')
    code_exts = {'java','xml','xslt','xsd','wsdl','py','groovy','yaml','yml',
                 'properties','json','sh','ts','js','sql','gradle','pom'}
    code_parts, langs = [], set()

    if os.path.exists(ff):
        for fn in sorted(os.listdir(ff)):
            ext = fn.rsplit('.', 1)[-1].lower() if '.' in fn else ''
            if ext in code_exts:
                fp  = os.path.join(ff, fn)
                txt = extract_text(fp, fn)
                if txt and not txt.startswith('['):
                    langs.add(ext)
                    code_parts.append(f'=== {fn} ===\n{txt[:8000]}')

    ctx_parts = []
    if proj.get('description'): ctx_parts.append(proj['description'])
    if proj.get('prompt'):      ctx_parts.append(proj['prompt'])
    for repo in proj.get('repos', []):
        if repo.get('url'): ctx_parts.append(f"Repositório: {repo['url']}")
    if proj.get('repo_url'):    ctx_parts.append(f"Repositório: {proj['repo_url']}")

    has_code  = len(code_parts) > 0
    code_ctx  = '\n\n'.join(code_parts)[:30000] if has_code else \
                'Código-fonte não disponível — análise baseada no contexto da integração.'
    extra_ctx = '\n'.join(ctx_parts)[:2000]
    lang_str  = ', '.join(sorted(langs)) if langs else 'inferido do contexto'

    set_status(pid, 'code_review', 70, f'Analisando {len(code_parts)} arquivo(s) com IA...')

    prompt = f"""Você é um especialista sênior em arquitetura de software, segurança (OWASP, CWE) e qualidade de código.

Integração: {gid}
Linguagens detectadas: {lang_str}
Contexto extra: {extra_ctx}

{"CÓDIGO-FONTE A ANALISAR:" if has_code else "CONTEXTO (sem código-fonte direto):"}
{code_ctx}

Faça análise PROFUNDA e DETALHADA. Retorne APENAS JSON válido, sem markdown:
{{
  "resumo": "Resumo técnico em 2-3 frases sobre qualidade e estado do código",
  "linguagens": ["{lang_str}"],
  "has_code": {str(has_code).lower()},
  "arquitetura": {{
    "padroes": ["padrão detectado 1", "padrão 2"],
    "estrutura": "Descrição da estrutura geral do código",
    "componentes_principais": ["NomeClasse: responsabilidade curta"]
  }},
  "qualidade": {{
    "score": 7,
    "nivel": "excelente|bom|regular|ruim",
    "pontos_fortes": ["aspecto positivo 1"],
    "pontos_fracos": ["aspecto negativo 1"]
  }},
  "insights_ia": [
    "Insight técnico relevante e não óbvio sobre o código/integração"
  ],
  "sugestoes_melhoria": [
    {{"prio": "alta|media|baixa", "titulo": "Título conciso", "desc": "O que melhorar e por quê", "fix": "Como implementar a melhoria"}}
  ],
  "riscos_seguranca": [
    {{"sev": "alta|media|baixa", "tipo": "ex: Credential Exposure / OWASP A02 / CWE-89", "desc": "Descrição do risco específico", "fix": "Como mitigar"}}
  ],
  "analise_preditiva": {{
    "bugs": [
      {{"prob": "alta|media|baixa", "desc": "Bug potencial identificado", "loc": "onde ocorre no código"}}
    ],
    "performance": [
      {{"impacto": "alto|medio|baixo", "desc": "Gargalo ou problema de performance", "fix": "Solução recomendada"}}
    ],
    "escalabilidade": "Análise sobre a capacidade de escalar este serviço sob carga crescente"
  }}
}}"""

    set_status(pid, 'code_review', 78, 'Finalizando análise de segurança e qualidade...')
    return _safe_call_json([{'role': 'user', 'content': prompt}], 4096)


# ── orquestrador principal ────────────────────────────────────────────────────

def _run_pipeline(pid: str):
    try:
        proj = load_project(pid)
        set_status(pid, 'start', 3, 'Iniciando pipeline CH8 Hub Build AI...')

        meta = _step_extract(pid, proj)
        proj['meta'] = meta; save_project(pid, proj)

        analysis = _step_analysis(pid, proj, meta)
        proj['analysis'] = analysis; save_project(pid, proj)

        contingency = _step_contingency(pid, proj, meta, analysis)
        proj['contingency'] = contingency; save_project(pid, proj)

        code_review = _step_code_review(pid, proj, meta, analysis)
        proj['code_review'] = code_review; save_project(pid, proj)

        diagrams = _step_diagrams(pid, proj, meta, analysis)
        proj['diagrams'] = diagrams; save_project(pid, proj)

        proj['status']     = 'done'
        proj['processing'] = {'step':'done','pct':100,
                              'msg':'Pipeline concluído com sucesso!',
                              'ts':time.strftime('%H:%M:%S')}
        proj['completed_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        save_project(pid, proj)

    except Exception as e:
        try:
            p = load_project(pid)
            p['status']     = 'error'
            p['processing'] = {'step':'error','pct':0,
                               'msg':f'Erro: {str(e)[:400]}',
                               'ts':time.strftime('%H:%M:%S')}
            save_project(pid, p)
        except Exception:
            pass
        raise

# ── API: raiz ─────────────────────────────────────────────────────────────────

@app.get('/')
async def root():
    return FileResponse(os.path.join(HERE, 'static', 'index.html'))

@app.get('/static/{path:path}')
async def serve_static(path: str):
    return FileResponse(os.path.join(HERE, 'static', path))

# ── API: projetos ─────────────────────────────────────────────────────────────

@app.get('/api/projects')
async def list_projects():
    items = []
    for pid in sorted(os.listdir(PROJECTS)):
        pf = os.path.join(PROJECTS, pid, 'project.json')
        if not os.path.exists(pf): continue
        with open(pf, encoding='utf-8') as f:
            p = json.load(f)
        ff = os.path.join(PROJECTS, pid, 'files')
        items.append({
            'id':         pid,
            'name':       p.get('name', pid),
            'status':     p.get('status', 'draft'),
            'created_at': p.get('created_at',''),
            'global_id':  p.get('meta',{}).get('global_id',''),
            'operacao':   p.get('meta',{}).get('operacao',''),
            'files_count': len(os.listdir(ff)) if os.path.exists(ff) else 0,
        })
    return items

@app.post('/api/projects')
async def create_project(
    name:        str = Form(''),
    description: str = Form(''),
    prompt:      str = Form(''),
    repo_url:    str = Form(''),
    repo_token:  str = Form(''),
):
    pid  = str(uuid.uuid4())[:8]
    proj = {
        'id': pid, 'name': name or f'Projeto {pid}',
        'description': description, 'prompt': prompt,
        'repo_url': repo_url, 'repo_token': repo_token,
        'status': 'draft',
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'processing': {'step':'idle','pct':0,'msg':'Aguardando início'},
        'meta':{}, 'analysis':{}, 'contingency':{}, 'diagrams':{},
    }
    save_project(pid, proj)
    return {'id': pid, 'name': proj['name']}

@app.get('/api/projects/{pid}')
async def get_project(pid: str):
    return load_project(pid)

@app.patch('/api/projects/{pid}')
async def update_project(pid: str,
    name:        Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    prompt:      Optional[str] = Form(None),
    repo_url:    Optional[str] = Form(None),
    repo_token:  Optional[str] = Form(None),
):
    p = load_project(pid)
    if name        is not None: p['name']        = name
    if description is not None: p['description'] = description
    if prompt      is not None: p['prompt']       = prompt
    if repo_url    is not None: p['repo_url']     = repo_url
    if repo_token  is not None: p['repo_token']   = repo_token
    save_project(pid, p)
    return p

@app.delete('/api/projects/{pid}')
async def delete_project(pid: str):
    folder = os.path.join(PROJECTS, pid)
    if os.path.exists(folder): shutil.rmtree(folder)
    return {'ok': True}

# ── API: arquivos ─────────────────────────────────────────────────────────────

@app.post('/api/projects/{pid}/files')
async def upload_files(pid: str, files: List[UploadFile] = File(...)):
    load_project(pid)  # validate exists
    folder = os.path.join(PROJECTS, pid, 'files')
    os.makedirs(folder, exist_ok=True)
    saved = []
    for f in files:
        dest = os.path.join(folder, f.filename)
        with open(dest, 'wb') as out: out.write(await f.read())
        saved.append({'name': f.filename, 'size': os.path.getsize(dest)})
    return {'uploaded': len(saved), 'files': saved}

@app.get('/api/projects/{pid}/files')
async def list_files(pid: str):
    folder = os.path.join(PROJECTS, pid, 'files')
    if not os.path.exists(folder): return []
    result = []
    for fn in sorted(os.listdir(folder)):
        fp  = os.path.join(folder, fn)
        ext = fn.rsplit('.', 1)[-1].lower() if '.' in fn else ''
        result.append({'name': fn, 'size': os.path.getsize(fp), 'ext': ext})
    return result

@app.delete('/api/projects/{pid}/files/{filename}')
async def delete_file(pid: str, filename: str):
    fp = os.path.join(PROJECTS, pid, 'files', filename)
    if os.path.exists(fp): os.remove(fp)
    return {'ok': True}

# ── API: repos ────────────────────────────────────────────────────────────────

@app.get('/api/projects/{pid}/repos')
async def list_repos(pid: str):
    p = load_project(pid)
    return p.get('repos', [])

@app.post('/api/projects/{pid}/repos')
async def add_repo(pid: str, body: dict):
    p = load_project(pid)
    repos = p.get('repos', [])
    repos.append({'label': body.get('label',''), 'url': body.get('url',''), 'token': body.get('token','')})
    p['repos'] = repos
    save_project(pid, p)
    return repos

@app.delete('/api/projects/{pid}/repos/{idx}')
async def del_repo(pid: str, idx: int):
    p = load_project(pid)
    repos = p.get('repos', [])
    if 0 <= idx < len(repos):
        repos.pop(idx)
    p['repos'] = repos
    save_project(pid, p)
    return repos

# ── API: processamento ────────────────────────────────────────────────────────

@app.post('/api/projects/{pid}/process')
async def start_process(pid: str, bg: BackgroundTasks):
    p = load_project(pid)
    p['status'] = 'processing'
    save_project(pid, p)
    bg.add_task(_run_pipeline, pid)
    return {'ok': True}

@app.get('/api/projects/{pid}/status')
async def get_status(pid: str):
    p = load_project(pid)
    return {
        'status':           p.get('status','draft'),
        'processing':       p.get('processing',{}),
        'has_meta':         bool(p.get('meta')),
        'has_analysis':     bool(p.get('analysis')),
        'has_contingency':  bool(p.get('contingency')),
        'has_code_review':  bool(p.get('code_review')),
        'has_diagrams':     bool(p.get('diagrams')),
    }

@app.post('/api/projects/{pid}/rerun/{step}')
async def rerun_step(pid: str, step: str, bg: BackgroundTasks):
    valid = {'extract','analysis','contingency','code_review','diagrams','all'}
    if step not in valid:
        raise HTTPException(400, f'Step inválido. Use: {valid}')
    p = load_project(pid)
    p['status'] = 'processing'
    save_project(pid, p)

    def _rerun():
        try:
            proj = load_project(pid)
            if step == 'all':
                _run_pipeline(pid); return
            if step == 'extract':
                proj['meta'] = _step_extract(pid, proj)
            elif step == 'analysis':
                proj['analysis'] = _step_analysis(pid, proj, proj.get('meta',{}))
            elif step == 'contingency':
                proj['contingency'] = _step_contingency(pid, proj, proj.get('meta',{}), proj.get('analysis',{}))
            elif step == 'code_review':
                proj['code_review'] = _step_code_review(pid, proj, proj.get('meta',{}), proj.get('analysis',{}))
            elif step == 'diagrams':
                proj['diagrams'] = _step_diagrams(pid, proj, proj.get('meta',{}), proj.get('analysis',{}))
            proj['status']     = 'done'
            proj['processing'] = {'step':'done','pct':100,'msg':f'Passo {step} concluído!','ts':time.strftime('%H:%M:%S')}
            save_project(pid, proj)
        except Exception as e:
            pp = load_project(pid)
            pp['status']     = 'error'
            pp['processing'] = {'step':'error','pct':0,'msg':str(e)[:400],'ts':time.strftime('%H:%M:%S')}
            save_project(pid, pp)

    bg.add_task(_rerun)
    return {'ok': True}

# ── API: export ───────────────────────────────────────────────────────────────

@app.get('/api/projects/{pid}/export')
async def export_project(pid: str):
    p = load_project(pid)
    if not p.get('meta',{}).get('global_id'):
        raise HTTPException(400, 'Projeto não processado ou sem global_id')
    meta = p['meta']; gid = meta['global_id']
    graph_node = {
        'id': gid, 'type': 'integration',
        'label':         meta.get('nome', gid),
        'familia_id':    meta.get('familia_id',''),
        'sistemas':      meta.get('sistemas_origem',[]) + meta.get('sistemas_destino',[]),
        'operacao':      meta.get('operacao',''),
        'sincronicidade':meta.get('sincronicidade','assincrona'),
        'protocolo':     meta.get('protocolo',[]),
        'padrao_eip':    meta.get('padrao_eip',[]),
        'descricao':     meta.get('descricao',''),
        'tags':          meta.get('tags',[]),
        'router_var':    meta.get('router_var',''),
        'fila_sap_pi':   meta.get('fila_sap_pi',''),
        'legs':          meta.get('legs',2),
        'analysis_ref':  f'analysis/analysis_{gid}.json',
    }
    analysis_file = {
        'id': gid, 'timestamp': p.get('completed_at', time.strftime('%Y-%m-%dT%H:%M:%S')),
        'analysis':    p.get('analysis',{}),
        'contingency': p.get('contingency',{}),
        'diagrams':    p.get('diagrams',{}),
        'source': 'ch8-builder',
    }
    return {'global_id': gid, 'graph_node': graph_node, 'analysis_file': analysis_file}

# ── API: chat ─────────────────────────────────────────────────────────────────

@app.post('/api/projects/{pid}/chat')
async def chat(pid: str, body: dict):
    p        = load_project(pid)
    message  = body.get('message','')
    history  = body.get('history',[])
    meta     = p.get('meta',{})
    analysis = p.get('analysis',{})

    ctx = (f"Você é um especialista sênior em integração SAP / Apache Camel / Red Hat Fuse / ActiveMQ.\n"
           f"Projeto em análise: {p.get('name','?')}\n"
           f"Global ID: {meta.get('global_id','?')} | Operação: {meta.get('operacao','')}\n"
           f"Sistemas: {meta.get('sistemas_origem',[])} → {meta.get('sistemas_destino',[])}\n"
           f"Chave de roteamento: {analysis.get('chave_roteamento',{}).get('campo','?')}\n"
           f"Descrição: {meta.get('descricao','')}\n"
           f"Responda em português, de forma clara, técnica e bem estruturada. Use markdown quando útil.")

    if not history:
        msgs = [{'role':'user','content': ctx + '\n\n---\n\n' + message}]
    else:
        msgs = [{'role':'user','content': ctx + '\n\n---\n\nVocê já está respondendo sobre esta integração.'}]
        msgs.append({'role':'assistant','content':'Entendido, pode perguntar.'})
        for h in history[-10:]:
            msgs.append({'role': h['role'], 'content': h['content']})
        msgs.append({'role':'user','content': message})

    reply = _call_bedrock(msgs, 2000)
    return {'response': reply}

# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import uvicorn
    uvicorn.run('server:app', host='0.0.0.0', port=8901, reload=False)
