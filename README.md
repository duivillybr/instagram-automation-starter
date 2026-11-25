# Instagram Automation Starter

Um ponto de partida em Python para preparar, revisar e agendar publicações em uma conta profissional do Instagram. O projeto demonstra fila persistente, integração opcional com IA, renderização de mídia e publicação pela API oficial da Meta.

**A entrada de conteúdo é flexível:** crie uma publicação manualmente pela CLI ou habilite o adaptador RSS de exemplo. RSS e notícias não são requisitos do modelo. A curadoria por IA está ligada ao fluxo de itens recebidos pelo adaptador RSS; publicações manuais são fornecidas pelo operador e exigem aprovação.

## Guia prático

O guia prático em português e inglês está disponível para download na conversa em que este starter foi preparado. Os PDFs não fazem parte deste repositório.

## Caso real

O [Portal Nexum no Instagram](https://www.instagram.com/portalnexum) é um caso real de automação editorial. O código e a configuração deste repositório são genéricos e não incluem logotipo, textos, feeds, credenciais ou configuração de produção do Portal Nexum.

## Arquitetura

```text
Conteúdo manual ─┐
                 ├─> fila SQLite -> revisão/aprovação -> card/Reel -> hospedagem pública -> API da Meta
Adaptador RSS ───┘                      └─> IA opcional para itens recebidos via feed
```

Os valores iniciais são seguros para desenvolvimento: simulação ligada, aprovação manual ligada, IA desligada e limite local de três publicações por 24 horas. A configuração de cada pessoa deve ser revisada antes de qualquer envio real.

## Requisitos

- Python 3.11 ou superior.
- FFmpeg e uma trilha de áudio licenciada pelo usuário apenas se `media.mode` for `reel` ou `mixed`.
- Conta profissional do Instagram, app Meta e permissões compatíveis para publicar de verdade.
- Cloudinary ou outro serviço equivalente configurado como hospedagem pública de mídia.
- Chave de API apenas se a IA estiver habilitada com `ai.provider = "openai"`. A opção Codex CLI usa a autenticação própria do Codex.

## Instalação local

```bash
python -m venv .venv
```

Ative o ambiente virtual e instale dependências:

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# Linux/macOS
source .venv/bin/activate

python -m pip install -r requirements.txt
```

Copie `.env.example` para `.env`. Preencha apenas as integrações que pretende usar. Edite `config.toml` com o nome, `@`, fuso, temas, janela e frequência próprios. Mantenha `dry_run = true` e `require_approval = true` durante a configuração.

Verifique a configuração e gere um card de demonstração sem publicar:

```bash
python -m instagram_automation doctor
python -m instagram_automation demo
```

## Criar uma publicação manual

Use o modelo de conteúdo em [`examples/post-modelo.md`](examples/post-modelo.md). Para preparar o card e a legenda:

```bash
python -m instagram_automation create-post --headline "Uma ideia em destaque" --caption-file examples/demo-caption.txt
```

O comando prepara a peça para revisão. Consulte a fila e o conteúdo, depois aprove explicitamente:

```bash
python -m instagram_automation queue
python -m instagram_automation show ID
python -m instagram_automation approve ID
python -m instagram_automation tick
```

Com `dry_run = true`, o último comando apenas simula o envio. Para publicar de verdade, configure credenciais válidas e permissões da Meta, revise a mídia pública e só então mude `dry_run = false`. A aprovação manual continua ativa por padrão. Conteúdo próprio pode ser publicado com a IA desligada; itens do adaptador RSS só são preparados e enviados com `ai.enabled = true`.

## Adaptador RSS e IA (opcionais)

1. Edite a entrada de exemplo `[[feeds]]` em `config.toml` com um feed e domínios que você controla ou tem autorização para consultar.
2. Ative o feed. O coletor usa RSS como uma possível fonte de conteúdo; outros adaptadores podem alimentar a fila.
3. Para curadoria de feeds, habilite `[ai].enabled` e configure o provedor. Com OpenAI, preencha `OPENAI_API_KEY` no `.env`; escolha um modelo disponível na sua conta.
4. Revise o texto e a mídia gerados. A IA não verifica fatos de forma independente.

Imagens, música, voz e vídeo precisam de licença ou autorização adequada. Dar crédito à fonte não concede automaticamente direito de republicação. O exemplo vem com download de imagens RSS desligado.

## Variáveis de ambiente

| Variável | Uso |
| --- | --- |
| `INSTAGRAM_USER_ID` | Identificador da conta profissional do Instagram |
| `INSTAGRAM_ACCESS_TOKEN` | Token compatível com o login configurado na Meta |
| `CLOUDINARY_CLOUD_NAME` | Nome da nuvem para upload das mídias |
| `CLOUDINARY_API_KEY` | Chave de API do Cloudinary |
| `CLOUDINARY_API_SECRET` | Segredo do Cloudinary |
| `OPENAI_API_KEY` | Opcional; necessária com IA habilitada e provedor `openai` |

Nunca grave valores reais no Git, em logs, prints de tela ou exemplos. Se uma chave for exposta, revogue-a e gere outra. Tokens de produção devem ser armazenados com acesso restrito.

## Personalização e operação

- Altere `config.toml` para temas, fuso horário, agenda, modelo de IA, limites e modo de mídia.
- A chamada à API da Meta é isolada em `instagram_automation/providers.py`; a coleta RSS fica separada em `instagram_automation/feeds.py`.
- Para usar Reels, instale FFmpeg e adicione em `assets/audio/background-loop.wav` uma trilha que você tenha direito de usar; o caminho e volume são configuráveis.
- O serviço mantém estado em SQLite e registra falhas para que uma publicação incerta seja reconciliada antes de uma nova tentativa.
- Para Linux, execute sob um usuário sem privilégios, com `systemd`, logs rotacionados, backup do banco e procedimento de rollback.
- Consulte a documentação atual da [Instagram API com Login Unificado](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/) e de [publicação de conteúdo](https://developers.facebook.com/documentation/instagram-platform/content-publishing/).

## Licença

MIT. Confira a licença dos serviços externos, das fontes de conteúdo e de cada arquivo de mídia que você adicionar.
