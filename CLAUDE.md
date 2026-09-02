# 貓狗寵物 30 秒領養廣告生成系統

## 專案簡介
自動化生成貓狗寵物 30 秒（含 15 秒版）領養宣傳短影音的系統。輸入為收容所/送養人上傳的照片、影片、基本資料與健康紀錄；輸出為含旁白（寵物第一人稱自我介紹）、字幕、配樂、可愛特效的成品短影音，並可直接發布至社群平台（TikTok、IG、YT Shorts、LINE、FB）。

目標使用者：收容所、動保團體、送養義工。核心目的：提升寵物曝光與領養轉換率。

> **這不是「文字轉影片工具」**，而是以寵物真實資料治理為核心的短影音生產平台：
> 資料整理 → 個性分析 → 腳本 → 分鏡 → 影音生成 → 審核 → 發布 → 成效回饋。
> 競爭力來自「真實資料治理＋人格化敘事＋可控分鏡＋人工審核＋領養成效閉環」，不是單一影片模型。

> 完整架構規格見 [docs/architecture.md](docs/architecture.md)（權威文件，細節一律以它為準）。
> Schema 範例：[docs/schemas/pet_profile.example.json](docs/schemas/pet_profile.example.json)、[docs/schemas/script.example.json](docs/schemas/script.example.json)
> Provider Adapter 介面參考：[docs/reference/provider_adapter.py](docs/reference/provider_adapter.py)

## 系統分層（對應 docs/architecture.md §1 架構圖）
1. **資料處理層**：上傳、素材品質檢查(VLM)、寵物辨識與特徵抽取、Identity Card 生成、素材分頻與最佳片段選取
2. **內容策劃層（多代理協作）**：Profile / Media Analyst / Persona / Marketing / Script（3種風格）/ Storyboard（拆5-7鏡頭）/ Fact-check 七個 Agent
3. **生成式 AI 層**：圖像處理/生成、鏡頭級影片生成(I2V/T2V)、TTS 語音生成、音樂/音效
4. **影容製作層**：素材整合、FFmpeg 剪輯、字幕/貼圖/特效、音訊混合、多尺寸版型輸出
5. **品質與安全審查層**：寵物一致性檢查(VLM)、事實正確性檢查、影音品質檢查、內容合規檢查（加權評分，見 §11）
6. **人工審核層**：三欄式介面（Profile／影片／分鏡+QA），影片預覽、腳本/旁白編輯、單鏡頭重新生成、核准/退回
7. **發布與輸出**：成品影片、封面圖、社群文案、字幕檔、素材與生成紀錄
8. **社群/平台發布**：TikTok、IG、Shorts、LINE、FB、官網/領養平台
9. **成效追蹤與優化**：全漏斗指標（曝光→3秒停留→完播→點擊→諮詢→申請→會面→領養）、A/B 測試、公平性監控

基礎設施：Auth/RBAC、Queue/Scheduler、任務狀態機、PostgreSQL、Object Storage、Redis、監控。
外部 AI 服務透過 **Provider Adapter** 抽象層接入，商用 API 與開源模型並列可替換（見下）。

## 關鍵設計決策（不要違背，除非使用者明確要求變更）
- **鏡頭級生成，不做一鏡到底**：每支影片拆成 5-7 個 3-6 秒鏡頭，各自獨立 Job、獨立生成、獨立 QA。失敗只重生失敗鏡頭，不整支重跑（架構理由見 docs/architecture.md §2, §10）
- **腳本一次產生 3 種風格**（萌系/溫暖故事/反差幽默）供人工挑選，不直接發布單一版本
- **Pet Profile 是唯一事實來源**：Fact-check Agent 逐句比對腳本與 Profile，任何內容不得捏造或隱藏必要照護限制條件（現況：`pipeline/fact_check.py` 已能查「漏了必要揭露」與「講了資料裡沒有的事」兩種，見下）
- **真實素材優先（策略 A）**：Image-to-Video（策略 B）僅在缺乏真實影片時補位；AI 幻想場景（策略 C）必須標示「部分畫面由 AI 創意生成」
- **QA 加權評分**（Identity Consistency 30% / Factual Correctness 25% / Visual Quality 15% / Audio & Subtitle 10% / Emotional Appeal 10% / Platform Compliance 10%）：低於 80 分不進人工發布畫面；事實正確性或合規檢查任一項失敗，無論總分多少都不可發布
- **人工審核為必經關卡**，不可跳過直接自動發布
- **字幕與文字元件一律由後製引擎疊加**，不讓生成模型在畫面內直接產生文字（避免錯字/字型變形）

## AI Provider 策略：商用 API × 開源模型並列
所有 AI 呼叫都必須經過 `Provider Adapter` 統一介面（見 [docs/reference/provider_adapter.py](docs/reference/provider_adapter.py)），禁止在業務程式碼中直接寫死特定廠商 SDK。

路由（Router）依序評估：**內容敏感度**（涉及真實寵物臉部/個資→優先本地開源）→ **時效性**（急件用商用、批次用開源排隊）→ **成本上限**（超過 cap 自動降級開源）→ **Fallback**（商用失敗/逾時→自動切開源重試）。

| 功能 | 商用 API | 開源方案 |
|---|---|---|
| LLM（腳本/文案/QA判斷） | Claude / GPT-4o | Llama 3.x、Qwen2.5、Mistral（vLLM） |
| VLM（素材檢查/特徵抽取） | GPT-4V / Claude Vision | Qwen2-VL、LLaVA-NeXT、CogVLM |
| 影片生成 I2V/T2V | Runway、Google Veo、Kling、Luma | Stable Video Diffusion、CogVideoX、**Wan2.2**、Open-Sora、Mochi 1 |
| 圖像生成/增強 | Midjourney API、GPT-Image | SDXL / Flux.1、Real-ESRGAN |
| TTS 旁白 | ElevenLabs、Azure/Google TTS | Coqui XTTS-v2、F5-TTS、GPT-SoVITS、Piper |
| 內容審核 | OpenAI Moderation API | LlamaGuard 3、Detoxify |
| 音樂/音效 | Suno/Udio API | MusicGen |
| Embedding | OpenAI/Cohere | BGE、E5 |

新增/替換 provider 時：在對應層的 `providers/` 目錄下實作統一介面（輸入輸出 schema 不變），並在 router 設定檔中登記路由規則，不修改呼叫端程式碼。

## 開發階段（Roadmap，詳見 docs/architecture.md §16）
- **PoC**：單一寵物、真實影片剪輯＋照片 Ken Burns 動態、一版腳本、自動字幕/音樂/CTA、人工核准下載
- **MVP**：多寵物管理、3種腳本風格、Image-to-Video、分鏡編輯、單鏡頭重生、影片 QA、多尺寸輸出
- **正式產品**：多組織權限、社群發布整合、成效儀表板、A/B 測試、多語言（含台語）、對外 API

素材組成建議比例（避免一開始做「完全生成式影片」）：60% 真實影片剪輯 / 20% 照片動態化 / 10% 字幕與貼圖 / 10% AI 生成場景。

目前所在階段：**MVP 開發中（第十二個切片：版型覆蓋層，Pillow 動態排版 ＋ FFmpeg 單次疊加）**。

- 第一個切片（多寵物管理／PostgreSQL 資料層）：`pipeline/db.py`／`pipeline/models.py`／`pipeline/pet_repo.py`，管理 CLI `pipeline/manage.py`（`init-db`／`import-profile`／`list-pets`／`show-pet`）
- 第二個切片（分鏡編輯／單鏡頭重生）：渲染邏輯抽到 `pipeline/rendering.py`（`render_script`，`generate_video` 與 `regenerate_scene` 共用，不重複寫一次），每次生成都在 `storage/output/<pet_id>/gen_<8碼token>/` 有自己的子資料夾（不再共用同一層、不會互相覆蓋鏡頭檔案）。`pipeline/regen.py` 提供 `apply_scene_overrides`（純函式）＋ `regenerate_scene`（patch 單一鏡頭素材/字幕/旁白後只重新渲染，不重跑 LLM），CLI 是 `pipeline/regenerate.py`。`GenerationJob` 現在存完整 `script_json`，`parent_job_id` 把重新生成的版本連回原始 job——**每次重生都是新的一筆紀錄，不覆蓋舊的**，原始輸出檔案與紀錄都保留供追溯。
- 第三個切片（Image-to-Video＋簡易 FastAPI/前端，含後續的表單化 UI 與背景任務）：
  - **I2V（策略 B）**：`providers/base.py` 新增 `VideoGenerationProvider`，目前有三個實作，透過 `pipeline/i2v.py` 的 `get_video_provider()` 以名稱切換（`svd`／`cogvideox`／`wan`）：
    - `providers/video/svd_provider.py`（SVD-XT，diffusers 本機推論）——**已在這張機器（RTX 5070 Ti，16GB VRAM）用元寶的照片跑過手動 smoke test**，確實會動，但 SVD 沒有文字條件，prompt 對它是 no-op，動的多半是鏡頭/背景而不是主體。
    - `providers/video/cogvideox_provider.py`（`CogVideoX-5b-I2V`——沒有官方 2B 的 I2V 檢查點）——只實作了介面，還沒手動跑過（5B 在 16GB VRAM 上吃緊，需要時再驗證）。
    - `providers/video/wan_provider.py`（**Wan2.2 TI2V-5B，Apache 2.0**）——文字 prompt 真的能指揮「主體」動作，是目前品質/速度最平衡的選擇。走**本機自架的 ComfyUI 伺服器**（`vendor/comfyui/`，自己的 venv，`vendor/` 已 gitignore），不是 diffusers、也不是 Wan 官方 `generate.py`：兩條路都試過並排除（diffusers 的 I2V-A14B 約 118GB 裝不下、TI2V-5B 在 diffusers 沒有 I2V 支援、官方 generate.py 只有 bf16 未量化版約 500s/step）；ComfyUI ＋ FP8 量化檢查點實測約 25s/step，一顆鏡頭（5 秒、20 步）約 **8 分鐘**、VRAM 約 11GB。完整理由寫在 `pipeline/config.py` 的 `WAN_*` 註解區與 `wan_provider.py` docstring。**這個 provider 不會自己啟動 ComfyUI**（跟 rendering 不會自己啟 Ollama/PostgreSQL 一樣），啟動指令見 [STARTUP.md](STARTUP.md)。
    `pipeline/rendering.py render_script()` 有 `animate_scenes`／`video_provider`／`animate_prompt` 參數：指定的照片鏡頭會先呼叫 I2V provider 產生暫存影片，再交給既有的 `build_scene_clip`（真實影片那條路徑：loop/裁切/固定 fps）處理，沒有另外寫一套邏輯。CLI 是 `pipeline/run.py --animate-scenes 2,4 --video-provider wan --animate-prompt "貓輕輕搖尾巴、抬頭看鏡頭"`、`pipeline/regenerate.py --animate --video-provider wan --animate-prompt ...`；網頁端對應 `GenerateRequest`／`RegenerateSceneRequest` 的 `animate_scenes`/`animate`、`video_provider`、`animate_prompt` 欄位。
  - **簡易 FastAPI/前端**：`webapp/main.py`（FastAPI，直接呼叫既有的 `pipeline.pet_repo`／`pipeline.run`／`pipeline.regen`，不重寫業務邏輯）＋ `webapp/static/index.html`（純 HTML/JS，無建置流程，不是架構文件最終目標的 React/Next.js）。**生成/重生改成背景執行**：`POST .../generate`、`POST .../regenerate-scene` 立刻回 202＋`task_id`，實際工作跑在 `webapp/tasks.py` 的背景執行緒，前端輪詢 `GET /api/tasks/{task_id}` 顯示進度條與目前步驟（腳本 1/3 → TTS → 鏡頭 n/m → 合併），可以關掉進度視窗繼續操作，重新整理頁面會用 `GET /api/tasks` 重新接上還在跑的工作。進度來源是 `pipeline/progress.py` 的 `on_progress` callback（CLI 不傳就是 no-op，行為不變）。**一次只跑一個**（生成會吃滿 GPU/CPU），第二個請求回 409。task 的**即時**狀態（進度百分比、目前步驟）刻意只存在 process 記憶體——重啟會連執行緒一起殺掉，存下「跑到 60%」並不代表任何還在進行的事。真正跨重啟的狀態在資料庫：`GenerationJob`／`SceneJob`（見上方 Job 狀態機段落），且 webapp 啟動時會跑 `reap_interrupted_jobs()`，把上一個 process 死掉時留在 `running` 的 job 收成 `failed`（否則 UI 會永遠顯示「生成中」），它們就會以「可續跑」的形式出現。唯一會判斷錯的情況是「CLI 正在跑的時候 webapp 剛好啟動」，但那是自癒的——CLI 仍持有那一列，跑完時 `finish_generation_job()` 會把狀態與錯誤清掉。網頁現在也能**從 `storage/profiles/` 底下的檔案路徑匯入 Profile**（`POST /api/pets/import-path`，路徑限制在 `storage/profiles/` 內＋ `PetProfile` pydantic 驗證，防止任意檔案讀取）和**直接編輯/新增 Pet Profile 的 JSON**（`PUT /api/pets/{pet_id}/profile`，同樣走 pydantic 驗證，upsert 語意跟 `pipeline.manage import-profile` 一致），對應測試在 `tests/test_webapp.py`。前端已做過一次介面改版（設計 token／深色模式、寵物清單搜尋、生成歷程版本卡片含 QA 警告、右側改成「產生新影片／單鏡頭重生」兩個分頁、選填欄位收進摺疊區、同步生成期間有計時的等待遮罩）；**單鏡頭重生改成用點選的**——先選版本再從鏡頭清單點一個鏡頭，不用自己記 job id/scene id，資料來源是新的 `GET /api/jobs/{job_id}`（回傳含 `script_json` 的完整 job 紀錄）。
  - **AI 背景生成（第四個切片）**：這是「讓影片有場景感、不只是照片配模糊邊」那條路的第一大步，分兩種處理方式，差別是編輯上的而不只是視覺上的：
    - **extend（延伸）**：保留照片真實的背景，只生成 9:16 畫面剩下的空白邊。相機拍到的東西一個都沒被換掉，等於策略 A 把黑邊補起來——最誠實的預設值，也不需要去背。
    - **replace（置換）**：把寵物切出來，整個場景重新生成，可以把牠放到牠從沒去過的地方。**動物是真的，地點是假的**，所以這是策略 C，鏡頭會被燒上「部分畫面由 AI 創意生成」的揭露標示。切出寵物的方式由 `config.BACKGROUND_MATTE_BACKEND` 決定：預設 **`sam3`**（文字提示分割，跟它說 `cat` 就只找貓）；`birefnet`（顯著物體去背，不需提示、檔案小）留給 SAM3 詞彙裡沒有的動物。**SAM3 要找什麼是由 pipeline 傳進去的**（`apply_background(subject=profile.species)`）——pipeline 知道物種、provider 不知道。
    - 兩者共通、也是最重要的一條：**最後一定會把寵物自己的像素貼回去**（`ImageCompositeMasked`）。取樣器只動遮罩區，但 `VAEDecode` 回傳的是整張從 latent 重建的畫面（照片也被 VAE round-trip 過，會變糊、偏色），不貼回去的話「寵物不會被重畫」就只是嘴上說說。
    - `providers/base.py` 的 `ImageEditingProvider` 有兩個方法（`outpaint_to_frame`／`replace_background`）＋ 一個吃 `mode` 的 `preflight()`（兩種處理需要的模型檔不一樣，只做 extend 的 run 不該被「缺去背模型」擋下來）。實作是 `providers/image/comfy_background_provider.py`，透過 `pipeline/background.py` 的 `get_image_provider()`／`apply_background()`／`BackgroundMode` 取用（provider 目前只有 `comfy`）。
    - 走**跟 Wan2.2 同一台 ComfyUI 伺服器**，幾乎全是**核心節點**（`ImagePadForOutpaint` → `SAM3_Detect`／`RemoveBackground` → `ThresholdMask` → `VAEEncodeForInpaint` → `KSampler` → `ImageCompositeMasked`），只多用 KJNodes 的 `GrowMaskWithBlur`（Wan 那條路已經需要 KJNodes）。要裝的只有兩個模型檔：SDXL checkpoint 放 `vendor/comfyui/models/checkpoints/`（`config.BACKGROUND_MODEL_FILE`）；replace 另外需要 SAM3（`config.BACKGROUND_SAM3_MODEL_FILE`，`Comfy-Org/sam3.1`，1.7GB，也放 checkpoints/）或 BiRefNet（`config.BACKGROUND_MATTE_MODEL_FILE`，`Comfy-Org/BiRefNet`，444MB，放 `models/background_removal/`），兩者 ComfyUI 都原生支援。缺哪個 `preflight()` 都會在開跑前講清楚檔案該放哪——而且是**照目前選的 backend** 講，指錯檔案會讓人跑去重載一個他已經有的東西。
    - 兩個 ComfyUI provider 共用的 HTTP 傳輸抽到 `providers/comfy_client.py`（`ComfyUIClient`：`ping`／`upload_image`／`run`／`fetch_output`／`node_options`），`wan_provider.py` 已改用它，各 provider 只負責描述自己的 graph。**graph 一律按節點參數名稱組，不要照 UI workflow 的 `widgets_values` 位置抄**（理由見 `wan_provider.py` docstring）。`node_options()` 要能讀**兩種** schema 形狀——舊節點把選項放第一個元素，用新 `IO.Schema` API 寫的節點放在旁邊的 options dict 裡；只讀前者會把「已經裝好的模型」誤報成「沒安裝」。
    - `render_script()` 新增 `background_scenes`／`background_mode`／`image_provider`／`background_prompt`，跟 `animate_scenes` 同一個模式。順序是**先處理背景再動態化**：兩個都指定的鏡頭會拿處理完的圖去跑 I2V。產出的圖存成 `work_dir/scene_<id>_bg.png` 並**快取重用**（續跑不會重付一次取樣）。真實影片與 recap 鏡頭不適用（判斷條件跟 I2V 共用的 `_is_single_photo`），列到清單裡只是被忽略，不算錯。
    - **揭露標示由 rendering 燒進畫面**（`build_scene_clip(disclosure_text=...)`，文字在 `config.BACKGROUND_DISCLOSURE_TEXT`），只有 replace 的鏡頭會有：extend 沒有換掉任何相機拍到的東西，每個補邊鏡頭都貼標示只會讓標示在真正該出現的地方失去意義。放在 rendering 而不是交給呼叫端，是為了讓它不可能被忘記。
    - `GenerationJob`／`SceneJob` 記 `background_scenes`／`background_mode`／`image_provider`／`background_prompt`（migration `2f878966c440` 建欄位、`45f1b54e4190` 改名並補 `background_mode`）。改名那份**刻意手改成 `alter_column` 而不是 autogenerate 的 drop+add**，否則改名前跑過的 job 會連設定一起被丟掉——那些列就是規範要求保留的生成紀錄。
    - **prompt 必須用英文**：SDXL 的文字編碼器是 CLIP、只認英文，中文 prompt 等同雜訊——實測中文的「溫暖的客廳」在貓上方生出了夜景城市天際線。而且**兩種模式要描述的東西相反**：extend 要描述「整張畫面」（含寵物，補的邊才會延續照片），replace 只描述「場景」——**prompt 裡提到動物，模型就會在旁邊再畫一隻**（實測過，第一次測試就多了一隻貓）。`config.BACKGROUND_NEGATIVE_PROMPT` 也擋掉「多生一隻動物／多生一個人」，那是對這隻寵物的**事實陳述**，不只是畫面難看。之後要讓非工程使用者能用中文描述，會需要一層翻譯（Ollama 已經在了），或等腳本層直接產生英文的 art direction。
    - **分割一定要在「補邊之前」做**：SAM3 要看的是照片本身，不是補了灰邊之後的畫布。實測同一張明顯有貓的照片，縮放後的圖量到 2.4%，加上灰邊之後變成 **0.0%**——照片本來就不會有那種灰邊，等於餵給它一張訓練時沒看過的東西。所以 matte 跑在 `ImageScale` 的輸出上，再用 `SolidMask` ＋ `MaskComposite` 把遮罩貼到整張畫布的對應位置。
    - 調參經驗（都在 `config.BACKGROUND_*`）：`SUBJECT_GROW` 是 **0**——放大去背遮罩會在寵物周圍留一圈「舊背景」，那是換背景最明顯的破綻；`MATTE_THRESHOLD` 先把 BiRefNet 的信心值二值化，否則隔著玻璃拍的照片整隻貓大約 0.5，貼回去會變成半透明的鬼影（實測）。KJNodes `GrowMaskWithBlur` 的 `fill_holes` 對乾淨的 matte 會回傳整片全白（等於「整張都是主體」，結果什麼都沒生成），它的第二個 inverted 輸出實測是空的——所以用核心 `InvertMask` 取補集。
    - CLI：`--background-scenes`／`--background-mode extend|replace`／`--background-prompt`／`--image-provider`（`pipeline.run`），`--background`／`--background-mode`／`--background-prompt`（`pipeline.regenerate`）。**網頁端還沒接**，這一步刻意只做 CLI。
    - 實測（RTX 5070 Ti 16GB）：一張 720x1280、25 步，約 **8 秒**（模型已載入），比 Wan2.2 的 8 分鐘便宜兩個數量級。
    - **replace 會擋下「照片裡根本沒有這隻動物」的情況**：跟 SAM3 要 cat 而照片裡沒有貓，它正確地回傳空遮罩，接著取樣器就會把**整個畫面**重畫——產出一支「草地很漂亮但沒有寵物」的領養影片（實測踩到：有人把一張路人的圖庫照片加進了 Profile 的素材清單）。所以 provider 會把主體遮罩存成 `<輸出>.mask.png`，用 FFmpeg `signalstats` 量它佔畫面的比例，低於 `config.BACKGROUND_MIN_SUBJECT_COVERAGE` 就**直接讓這顆鏡頭失敗並指名是哪個素材**。這順便也是「這張照片不是這隻寵物」的檢查。
    - **replace 的已知限制**：原本躺在床上/架子上的寵物換到草地後會「浮」在半空，因為姿勢與新地面的透視對不上，也沒有接觸陰影——用**模糊/淺景深的場景**明顯比有清楚地面的場景安全。（另一個「連貓跳台一起去背」的問題已由 SAM3 解掉。）另外 replace 會**把爛照片的缺陷放大**：隔著玻璃拍、本身就霧霧的照片，切出來貼到乾淨的生成場景上會更明顯。
  - **背景進入腳本層（第五個切片）**：這是「有故事性」真正落地的地方。上一個切片裡整支片共用一句 prompt，所以背景是「同一個場景重複六次」，不是一條故事線。現在**每個鏡頭自己帶背景**，由腳本 LLM 一起產生：
    - script schema 新增 `story_arc`（全片一句話的場景走向）、`art_direction`（全片共用的視覺風格，英文）、以及每個 scene 的 `background: {mode, prompt}`。`mode` 是 `keep`／`extend`／`replace`（`pipeline/background.py` 的 `BackgroundMode`，新增了 `keep`）。範例見 [docs/schemas/script.example.json](docs/schemas/script.example.json)。
    - **`art_direction` 會被接到每一個鏡頭的 prompt 後面**（`resolve_scene_background`），這是讓六個鏡頭看起來像同一支片而不是六支的關鍵；鏡頭自己的描述在前，風格在後。
    - **決定順序**：CLI 的 `--background-scenes` 指名的鏡頭 > 腳本自己的 `background` 區塊 > 什麼都不做。理由是前者是「審核的人在修某一顆鏡頭」，那必須壓過腳本的決定。純函式 `resolve_scene_background()` 就是這條規則，測試在 `tests/test_scene_backgrounds.py`。
    - **腳本 prompt 新增的規則**（`pipeline/script_gen.py`）：影片素材一律 `keep`；prompt 一律英文；`extend` 描述整張畫面、`replace` 只描述場景且**不可提到任何動物**；`replace` 優先選散景/淺景深（有清楚地面的場景會讓寵物看起來浮在半空）；各鏡頭 prompt 不可重複；至少要有一個鏡頭是 `keep` 或 `extend`。實測 Qwen2.5-7B 能穩定產出這個結構。
    - **fact-check 納入背景**（`find_background_risks()`）：`replace` 的場景是創作，但它仍然在宣稱事情——畫面裡有小孩等於宣稱這隻寵物親近小孩，出現診所等於宣稱健康狀況，Profile 都沒說過。禁用字清單在 `config.BACKGROUND_FORBIDDEN_TERMS`（整個字比對，`human` 不會誤中 `humid`）。只檢查 `replace`：`extend` 延續的是相機真的拍到的東西。
    - **QA 新增三項**（`pipeline/qa.py`）：未知的 mode、要了處理卻沒寫描述、以及**多個鏡頭共用同一句背景描述**（那就是舊的單一 prompt 行為套上新 schema）、整支片每顆鏡頭都是 `replace`（領養影片至少要有一顆是牠真正待過的地方）。
    - 生成紀錄：`disclosure_missing` 這個 JSONB 現在存兩個 key（`missing_restrictions`／`background_risks`），兩者分開是因為修法不同——一個要改旁白，一個要改背景描述。網頁的版本卡片會分開顯示。**沒有 migration**：欄位本來就是 JSONB。
    - **網頁端已接上**：`GenerateRequest` 多了 `background_scenes`／`background_mode`／`background_prompt`／`image_provider`，`RegenerateSceneRequest` 多了 `generate_background`／`background_mode`／`background_prompt`／`image_provider`。表單裡是「AI 背景（選填，覆寫腳本的決定）」摺疊區——**留白是正常狀態**，那代表每顆鏡頭照腳本自己的背景做。單鏡頭重生沿用跟動態化一樣的「一個控制項」規則：選了處理方式就等於啟用（沒有另一個獨立的勾選框可以互相矛盾）。填了描述卻沒指定鏡頭/沒啟用會被擋下來並說明原因，理由跟 `animate_prompt` 那條一樣：默默丟掉設定會讓人以為「這個功能沒作用」。鏡頭清單每一列也會標出該鏡頭的背景處理方式，`keep` 不標（每列都有徽章等於沒有徽章）。
  - **一致性檢查（第六個切片，VLM）**：docs/architecture.md §11 把 Identity Consistency 排在 QA 加權的最前面（30%），而在這之前完全沒有任何自動檢查。它抓三種「人一眼看得出、但其他檢查都抓不到」的失敗：**寵物不見了**（分割沒找到東西，取樣器把整個畫面重畫）、**多了一隻不存在的動物**、**那不是這隻動物**（物種不對，或被 I2V 扭曲到認不出來）。
    - `providers/base.py` 新增 `VLMProvider`（`inspect_image(image, prompt) -> str`，跟 `LLMProvider.complete` 一樣薄），實作 `providers/vlm/ollama_vlm_provider.py`（預設 `gemma3:12b`，跟腳本 LLM 同一台 Ollama）。判斷邏輯在 `pipeline/identity.py`，純函式 `_judge()` 可以不用 GPU 測。
    - **只問「一張圖」，然後跟 Profile 比對，不做兩張圖比對**。兩張圖那條路先試過而且會騙人：跟它說「第一張是參考、第二張是影格」，它會回答第一張的內容——一張完全沒有動物的影格被它回報成「同一隻貓、信心 0.95、沒有問題」。給它一張圖時它很準（三個案例分別數出 1／3／0 隻動物，還主動指出貓看起來浮在草地上）。而且比對 Profile 本來就是對的：Profile 是唯一事實來源。
    - **prompt 不可以把答案列給它看**。第一版在 schema 裡放了一個 `problems` free-text 欄位並舉例「肢體扭曲、邊緣融合不良…」，結果它每張圖都原封不動抄回那幾句，好圖也照抄——它在接話，不是在看圖。改成一個數量 ＋ 兩個是非題（`body_intact`／`sits_in_the_scene`）之後，答案才會隨圖片不同。
    - **兩種發現的份量不一樣，措辭也不一樣**：畫面裡有幾隻動物、是什麼物種，接近算術，模型很可靠，所以直接陳述；「看起來像貼上去的」是判斷，措辭是「請確認這顆鏡頭」。把判斷寫成定論會教會審核者忽略警告。
    - **只回報、不擋下**：判斷來自本機小模型，為了它的意見丟掉一支跑了好幾分鐘的影片並不划算。整個檢查（讀影格、連模型、解析回答）包在同一個 try 裡，檢查不了就記成「需要人工確認」，絕不往外丟例外。
    - 只跑在**畫面被生成過**的鏡頭（有背景處理或有 I2V）。照片做 Ken Burns 的鏡頭就是那張照片，沒有東西會跑掉。檢查的是「進 `build_scene_clip` 之前的那張圖」，不是成品 clip——不然燒上去的字幕和揭露標示也會被模型讀進去。
    - 結果存在 `SceneJob.identity_check`（JSONB，migration `5a3057c1499a`），重試會清掉（新的畫面，舊的判斷不再描述任何存在的東西）。網頁的鏡頭清單會把被標記的鏡頭標紅。
    - **已知誤判**：貓身後有一櫃絨毛娃娃時，第一版把娃娃算成第二隻動物。prompt 已補上「絨毛玩具、公仔、印刷圖案、布料上的圖案都不算動物」，實測那張圖就正確回報 1 隻了。這類事情靠 prompt 定義用詞可以解，但不要指望它完美——它是給人看的警告，不是判決。
    - 測試預設**關掉**這個檢查（`tests/conftest.py` 的 autouse fixture）：它要呼叫多模態模型、每顆鏡頭好幾秒，對「這幾顆鏡頭有沒有被渲染」的測試來說是純負擔。
  - **事實查核升級成語意比對（第七個切片）**：`pipeline/fact_check.py` 原本是純字串比對，它自己的 docstring 就寫了會誤判。現在有兩項檢查，都吃一個可選的 `LLMProvider`（不給就退回原本的行為）：
    - `find_missing_disclosures()`：**先字串、再模型，而且模型只能「放行」不能「定罪」**。字面出現＝一定有揭露，這是確定的、免費的，而且涵蓋大部分情況；只有字串比對找不到的那幾條才送去問模型——因為那個檢查只有一種失敗方式：**把換句話說的揭露誤報成沒說到**。實測（真的 LLM）：「需要長期服用腎臟處方飼料，不可中斷」被寫成「我需長期服用腎臟處方飼料喔」時，字串比對報缺少、模型正確放行；整條被刪掉時模型也正確地維持「缺少」。模型不可用或答得亂七八糟時**維持字串比對的判斷**（也就是退回「請人看一下」，而不是退回沉默）。
    - `find_unsupported_claims()`：**這是原本完全沒有的一半**——前者問「該說的有沒有漏掉」，這個問「有沒有講了資料裡沒有的事」。一支影片捏造「我最愛跟小孩玩」，就是領養人把一隻沒人跟他描述過的動物帶回家的方式。字串比對對這件事完全無能為力（捏造的句子跟真的句子長得一模一樣），所以沒有 fast path；沒有模型就回空的，不猜。實測會抓到「親近小孩」「會握手坐下」，而且正確忽略 CTA 那類呼籲。
    - 兩項都**只回報不擋下**，而且**由寫腳本的同一個模型來查自己寫的腳本**——這比獨立審查弱，也正是全部只回報不擋下的原因。
    - 存在 `disclosure_missing` 這個 JSONB 的第三個 key（`unsupported_claims`），**沒有 migration**。網頁版本卡片三種問題分開顯示，因為修法不同：漏了要**補進旁白**、捏造要**從旁白刪掉**、背景有問題要**改背景描述**。
    - 單鏡頭重生也會跑（`pipeline/regen.py`）：那正是審核者自己的文字進到影片裡的地方。這是一次小呼叫，不是重生刻意要避開的腳本生成。
  - **人工審核關卡（第八個切片）**：前面每一項檢查都只印警告——一支有三句捏造內容的影片，跑完之後看起來跟乾淨的一模一樣，而且**沒有任何地方記錄有人看過**。這一片把檢查結果變成一道真的關卡。
    - **審核狀態是自己的欄位，不是新的 `JobStatus` 值**（`pipeline/review.py` 的 `ReviewState`：`pending`／`approved`／`rejected`）。`JobStatus` 講的是「這次執行有沒有跑完」，一個 job 檔案一產生就是 `done`，但在有人看過之前一直是 `pending`——把兩者混在一起會讓 `done` 同時代表兩件事。
    - **哪些發現會擋下核准**（`publication_blockers()`）：`missing_restrictions`／`unsupported_claims`／`background_risks`。這三種都是「對一隻真實動物說了資料不支持的話」，也就是 CLAUDE.md 裡「事實正確性或合規檢查任一項失敗，無論總分多少都不可發布」那條。**結構問題與一致性檢查的發現刻意不擋**——那些是品質判斷（VLM 覺得像貼上去的鏡頭可能完全可以接受），判斷權在看畫面的人手上。不能被放行的只有「說了不是真的的事」。
    - **規則寫在 repository 層，不是瀏覽器**（`pet_repo.approve_generation_job()` 會拒絕）。只存在於 UI 的規則不是規則；前端另外先問 `GET /api/jobs/{id}/blockers` 只是為了讓人**看到原因**，而不是按下去才被拒絕。
    - **退回一定要寫原因**（前後端都擋）。沒有原因的退回等於什麼都沒告訴下一次生成，下一輪就只能用猜的。
    - 網頁版本卡片：`pending` 不標（那是常態），核准/退回才有徽章；退回的卡片會顯示原因；只有 `pending` 且已完成、未清理的版本才會出現「核准／退回」按鈕。
    - 寫這一片時抓到一個真的 bug：`get_generation_job()` 當初就沒有回傳 `disclosure_missing`／`structure_issues`（清單有、單筆讀取沒有），所以關卡讀不到任何發現、什麼都擋不住。已補上。
    - 這道關卡目前**還沒有下游**——發布功能還沒做。它現在的價值是：留下「有人看過並負責」的紀錄（規範要求的必經關卡），以及給審核者一份待審清單。等社群發布做出來時，它就是那個前置條件。
  - **版面裝飾層＋背景置換降級（第九個切片）**：實測發現 `replace` 的品質**完全取決於那張照片好不好去背**——元寶四腳伸長趴在淺色床單上、毛色跟床單同一個亮度，SAM3 只抓到 4.89% 的一小塊，合成後貼回去的只有那一小塊真貓，其餘整張被取樣器重畫成一隻**生成的大貓**，真貓碎片黏在牠臉上。而「找不到寵物就中止」的門檻（0.5%）防的是「完全找不到」，分辨不出「只找到一部分」。
    - **`config.BACKGROUND_ALLOW_SCRIPT_REPLACE` 預設 0：腳本只能選 `keep`／`extend`**。理由是資訊上的，不是品質上的——**腳本模型看不到照片**，它只讀得到檔名和 Profile，不可能判斷「這張適不適合去背」。`keep`／`extend` 都不碰相機拍到的像素，不會這樣壞掉。腳本若仍寫了 `replace`，`resolve_scene_background()` 會**降級成 `extend`**（鏡頭照樣做出來），並由 `pipeline/qa.py` 回報，不會靜悄悄發生。置換保留給**看過那張照片的人**在審核介面上針對單一鏡頭指定。
    - **`pipeline/decoration.py`（版面裝飾）**：內縮邊框、柔和暈影、以及開場前 3 秒的寵物資訊卡（名字·年齡·性別·品種）。全部是 FFmpeg 合成、**不經過任何模型**——同樣輸入永遠同樣輸出、不可能改到寵物、不需要揭露標示，這正是它跟背景置換的差別。跟字幕燒在同一條 filter chain 裡，不多一次編碼。
    - **用哪一套外觀是照腳本的 `style` 決定的**（萌系/溫暖故事/反差幽默各一個主色）。創作決定已經寫在腳本裡（符合「畫面上的創作決定要寫在腳本裡」那條），而每個 style **長什麼樣**是設計系統，屬於 `config.DECOR_*`，不是讓 7B 模型每次自己編。
    - 邊框是**畫在畫面內**（drawbox）而不是外加，每個 clip 都必須維持一模一樣的輸出尺寸，否則 concat 就不能再 stream-copy。
    - **字幕換行**（`wrap_burned_text`）：drawtext 不會自動換行，超過畫面寬度就是左右被裁掉、而且不報錯。實測腳本模型寫出 46 字的英文字幕時整句被切掉兩端。中文按字斷、英文按空白斷，一個中文字算 2 個半形單位。字幕的 y 座標改成**以文字區塊底部對齊**（`y=h-200-text_h`），兩行字幕才會往上長而不是掉出畫面。
    - 這次也修掉一個**我自己造成的回歸**：背景規則裡「一律用英文」講太多次，7B 模型把它套用到整個輸出，`subtitle` 全變成英文。規則已改成明確限定範圍——只有 `background.prompt` 和 `art_direction` 是英文，`narration`／`subtitle`／`title`／`story_arc` 一律繁體中文。
  - **外觀調整工具＋方向檢查（第十個切片）**：邊框主色與粗細變成網頁上可以調的東西，而不是只能改 `.env`。
    - `GenerateRequest`／`RegenerateSceneRequest` 多了 `accent_colour`／`border_width`，存進 `GenerationJob.decor_accent`／`decor_border_width`（migration `0d2302d7007d`）。跟其他設定同一個理由：**續跑必須把同一支影片做完**，半支換了顏色就是另一支影片。留白＝照風格的預設色。
    - **`GET /api/pets/{id}/decor-preview` 即時預覽**：用**同一個 `build_scene_clip`** 渲染 0.2 秒再抽一張影格，約 **450ms**。走同一條渲染流程是刻意的——預覽跟實際輸出不可能對不起來。用整支影片來調邊框等於每次猜要等三分鐘，那不叫調整。
    - 資訊卡在有 AI 揭露標示時會往下讓開（`config.DECOR_DISCLOSURE_CLEARANCE`）：兩個都在畫面上方，貼在一起會看起來像一塊亂糟糟的東西（實測）。
    - **一致性檢查補上「方向」這一題**：實測一顆「從上往下拍的貓」被合成進「平視角的房間」，VLM 的描述是 *"The cat is playfully falling over"*——它看到了，但**判定通過**，因為我只問了「幾隻／身體完不完整／有沒有在場景裡」，而貓確實躺在地毯上、身體也完整。**沒有人去讀那句描述。** 現在多問一題 `upright_in_the_scene`，同一張圖就被標記了。
    - 這也是 `replace` 真正的結構性問題：**背景生成器對照片的拍攝角度、主體比例、地面在哪裡一無所知**。SDXL 想畫平視角的房間就畫，照片是俯拍的貓，兩個視角根本不相容。這不是調參數能修的，也正是它被降級成人工專用的原因。
  - **貼圖層（第十一個切片）**：`pipeline/stickers.py`——愛心、爪印、閃亮，疊在畫面上。
    - **為什麼貼圖可以、卡通背景不行**：這些影片的寵物是**照片**。寫實的動物擺在插畫場景上，比純寫實或純插畫都糟——那就是一致性檢查在抓的「像貼上去的」。角落一個平面小圖不會跟照片打架，它是在框住照片，而那正是社群寵物影片實際的做法。
    - **圖形是用程式畫的（Pillow），不是美術素材**：三個理由——版控裡不放二進位檔、可以**跟著影片的主色上色**（所以貼圖屬於這支片，不是疊在上面的外來物）、而且是決定性的（同一支片永遠同一組）。快取在 `storage/decor/`，用 shape+colour+size 當 key。**這不是設計師貼圖組的替代品**；哪天有手繪素材，可以直接放進同樣的疊加位置。
    - **位置避開文字**：畫面上方是資訊卡＋AI 揭露標示、下方是字幕，貼圖只能落在中間那條帶狀區域的邊緣（`config.DECOR_STICKER_SAFE_TOP`／`SAFE_BOTTOM`）。而且**位置會隨鏡頭移動**——同一個圖釘在同一個角落六次，那叫浮水印不叫裝飾。
    - **數量依風格**：萌系兩個（愛心＋閃亮）、反差幽默兩個（閃亮）、**溫暖故事只有一個爪印**——一支講辛苦過的動物的片子不該灑滿星星。
    - 疊加用 FFmpeg 的 `movie` 來源接在既有 filter chain 後面，不需要多開輸入、也不用改成 filter_complex。貼圖放**最後**（文字之後）：圖蓋到字幕比字幕蓋到圖糟。
    - `pillow` 因此進了主要依賴（原本只在 `[i2v]` extras 裡）。
  - **版型覆蓋層（第十二個切片）**：`pipeline/overlay_renderer.py`——資訊欄、對話氣泡、置中大字、結尾行動卡。這是 §4「字幕/貼圖/特效」裡 drawtext 做不到的那一半。
    - **為什麼不是 drawbox＋drawtext**：字幕是「一個字串放在一個位置」，drawtext 剛好；版面不是——圓角半透明底板的**高度取決於文字換行後有幾行**、氣泡要有指向寵物的尖角、每一行要對著底板寬度量。用 FFmpeg 表達等於在已經很長的 filter chain 裡用表達式語法算每一個座標，而且**FFmpeg 沒辦法回答「這個字串會有多寬」**。
    - **分工跟 `stickers.py` 同一個模式，只是複雜一級**：Pillow 排版（因為它量得到文字）→ 存成一張全畫面透明 PNG → `build_scene_clip` 用 `movie=` 接在既有 `-vf` 鏈尾端疊上去。**沒有改成第二個 `-i` ＋ `filter_complex`**：`movie=` 就是貼圖已經在用的機制，能維持單一 `-vf`、單次編碼，也不用重寫指令組裝。疊在**最上層**（文字、貼圖之後），因為它是畫面上資訊密度最高的一層。
    - **spec 放在 `overlay_renderer.py`，不放 `pipeline/models.py`**：後者是 SQLAlchemy ORM 的資料表定義（`tests/test_migrations.py` 拿它跟真實 DB schema 比對），放一個 pydantic 腳本模型進去是類別錯誤。位置比照 `background.py`（`SceneBackground` 跟 `resolve_scene_background` 同一個模組）。
    - **schema**：每個 scene 多一個 `overlay: {template, headline, quote, tags[], cta_text, contact_info}`，`template` 是 `none`／`center_quote`／`speech_bubble`／`info_sidebar`／`contact_card`。**只有文案，沒有座標/顏色/字級**——那些是設計系統，在 `config.OVERLAY_*`，不是讓 7B 模型每次自己編（跟 `DECOR_*` 同一條理由）。
    - **腳本 prompt 按「鏡頭的功能」分配版型，不是按鏡頭編號**：這裡的鏡頭數是 5-7（`config.MIN_SCENES`/`MAX_SCENES`），寫死「Scene 1/2/3」會對不上。規則是 hook 用 `center_quote`、個性鏡頭用 `speech_bubble`、基本資料用 `info_sidebar`、最後一顆用 `contact_card`，其餘 `none`。
    - **版面文字會被事實查核**（`fact_check._script_text()` 現在把 overlay 的文字一起讀進去）：燒在畫面上的「疫苗：已完成」跟旁白講出來的完全一樣是對一隻真實動物的陳述，而且**在靜音觀看的平台上，畫面上那份才是真正被接收到的**。不納入的話，版面就會是唯一一個捏造內容抓不到的地方。反過來也成立：只寫在版面上的必要揭露算有揭露。
    - **QA 新增四項**（`pipeline/qa.py`）：未知的 template、選了版型卻沒填必要欄位（那塊板子會靜悄悄消失）、overlay 不是物件、以及**超過一半的鏡頭都有版面**（每顆都貼就不是強調，只是一面蓋住寵物的文字牆）。
    - **字型是 `config.OVERLAY_FONT_FILE`，預設就是字幕用的那個**（`DRAWTEXT_FONT_FILE`，msjh）：同一個畫面兩種字型看起來像兩支影片疊在一起。也**不新增字型檔**。
    - 實測踩到並修掉的兩件事：**emoji 會變成空白方框**（微軟正黑體沒有 emoji 字符，「預約見面 🐾」變成「預約見面 □」，看起來像影片壞掉而不是少了裝飾）——所以 `strip_unrenderable()` 依 Unicode 區段濾掉，**用區段而不是探測字型覆蓋率**，否則不同機器會產出不同畫面；prompt 也加了「不要用 emoji」，但比照 `SUBTITLE_MAX_UNITS` 那條，**畫面不可以依賴 7B 模型聽話**。以及**結尾行動卡貼著字幕**——安全帶的下緣是為貼圖（小、稀疏）訂的，一塊板子直接坐在上面會跟字幕連成一塊，而字幕是往上長的，所以多了 `config.OVERLAY_SUBTITLE_CLEARANCE`。
    - **沒有 migration、也沒有新的欄位**：版面是腳本的一部分，跟著既有的 `script_json` 走，續跑/單鏡頭重生自動帶著。PNG 畫在該次生成的 `work_dir/scene_<id>_overlay.png`，**不跨次快取**——它只要幾毫秒，而續跑本來就重用整支完成的 clip，快取唯一能做的事是在審核者改完文案後端出一張舊的。
    - **網頁端已接上（單鏡頭重生）**：`RegenerateSceneRequest` 多了一個 `overlay` 欄位，型別直接是 `SceneOverlaySpec`。**刻意是「一個可為 null 的物件」而不是一堆 `overlay_*` 字串**——欄位是屬於某個版型的（換成氣泡卻留著舊的 headline，會讓那段文案留在 scene 上不被渲染卻仍被事實查核讀到），而且一堆字串只能表達「不要改」，沒辦法表達**「把版面拿掉」**（那是 `template: "none"`，審核者看到一個不好看的版面必須能移除它）。`apply_scene_overrides()` 因此是整組替換而不是逐欄合併。
    - 表單在「版型（選填）」摺疊區：下拉選版型，**只顯示該版型真的會渲染的欄位**（四個欄位同時給等於三個會被丟掉，而被丟掉的文案事後讀起來就是「這個功能沒作用」，跟 `animate_prompt`／`background_prompt` 同一條規則）。點選鏡頭時會**用該鏡頭現有的版面預填**，鏡頭清單每一列也會標出目前的版型（`none` 不標）。填了文案卻選「不加版面」、或選了版型卻沒填內容，前後端都會擋下並說明原因。
    - **沒有 migration、也沒有 `regenerate_scene` 以外的新參數**：版面在 `script_json` 裡，續跑/重生本來就帶著走。
    - 實測（job 3476 → 3628，一支原本完全沒有版面的影片）：加上 `center_quote` 後只有第 1 顆鏡頭多出 `scene_1_overlay.png`，其餘鏡頭畫面不變，邊框/暈影/資訊卡/貼圖/字幕/版面全部在同一次編碼裡合成。

  - **給非工程使用者的表單化 UI**：Pet Profile 不再只有 JSON textarea——`webapp/static/index.html` 現在是中文欄位表單（基本資料／健康狀態勾選／個性標籤 chip 編輯器／故事與領養條件／照片影片清單含縮圖／外觀特徵摺疊區），JSON 直編退居「進階」摺疊區（可「套用到上方表單」，但仍要按表單的儲存鈕才寫入 DB）。表單會保留 schema 中沒有對應 widget 的欄位再合併回去，不會靜默丟資料。**檔案路徑欄位改成用選的**：瀏覽器拿不到本機檔案的真實路徑，所以「從電腦選擇」＝開 OS 檔案對話框→上傳到 `storage/assets/<pet_id>/`→用存下來那份的相對路徑；新增 `POST /api/pets/{pet_id}/assets`（副檔名 allowlist、檔名淨化、同名不覆蓋、pet_id 先做字元檢查＋ `storage/assets/` 包含性檢查再查 DB）、`GET /api/pets/{pet_id}/assets`（列出已上傳檔案給下拉選單）、`GET /api/profile-files`（匯入表單改成下拉選單），照片縮圖走唯讀的 `/media` static mount。單鏡頭重生的「換素材」也從手打 asset_id 改成從該寵物素材下拉選。上傳需要先有 pet_id（新寵物要先存檔才能傳照片）。

`GenerationJob` 現在是**開跑就建檔**：`start_generation_job()` 在慢工作開始前先寫一筆 `status=running`，結束時 `finish_generation_job()`（`done`＋ output_path/script_json）或 `fail_generation_job()`（`failed`＋錯誤原因）收尾，所以跑到一半崩潰／重啟不再是「完全沒紀錄」。狀態值是 `pipeline/models.py` 的 `JobStatus`（`running`／`done`／`failed`）。

**鏡頭級 Job（`SceneJob`）**：`scene_jobs` 一列一顆鏡頭，記錄狀態、重試次數、用了哪個素材／I2V provider／prompt（滿足「每支影片都要留生成紀錄」那條規範；seed 還沒記，因為目前沒有 provider 會把它回傳給呼叫端）。這帶來**續跑**：`python -m pipeline.resume <job_id>`（或網頁上失敗版本卡片的「↻ 從失敗的鏡頭續跑」）會沿用該 job `work_dir` 底下已完成的鏡頭 clip，只補跑沒做完的——一顆 Wan2.2 鏡頭要 8 分鐘，這是重點。續跑**沒有任何參數**：腳本、voice_sample、music_track、animate_scenes/video_provider/animate_prompt 全部存在 job 上，從那裡讀，避免續跑產出一支「不一樣的影片」。

`pipeline/rendering.py` 刻意**不 import 資料庫**：鏡頭狀態透過 `scene_tracker` 參數注入（`pipeline/scene_tracking.py` 的 `NoopSceneTracker`／`DatabaseSceneTracker`），跟 `on_progress` 同一個模式——CLI/測試用 no-op，pipeline 用 DB 版。I2V provider 改成**延遲載入**，續跑時若動態化鏡頭都已完成就完全不載模型。

這仍**只是** docs/architecture.md §10 的一個子集：§10 的 PUBLISHED 等狀態要等社群發布做出來才有意義，刻意不先刻空狀態；`PENDING` 要等真的有佇列（目前 job 一建立就在跑）。人工核准已經做了，但走的是另一個軸（`GenerationJob.review_state`，見下方「人工審核關卡」），不是 `JobStatus` 的新值。開發時請先確認目前實際完成到哪個階段，不要假設後期功能已存在。

**Schema 演進走 Alembic**（`migrations/`）：改了 `pipeline/models.py` 的既有表結構就要跟著出一份 migration——
```bash
alembic revision --autogenerate -m "<改了什麼>"   # 對照 models.py 與現有 DB 產生差異
alembic upgrade head                              # 套用
```
連線字串**刻意不寫在 `alembic.ini`**（那個檔進版控、URL 帶密碼），由 `migrations/env.py` 從 `pipeline.config.DATABASE_URL` 注入。`tests/test_migrations.py` 會擋下「改了 model 卻忘記寫 migration」（比對 `models.py` 與實際 DB schema）以及 `alembic.ini` 裡意外出現連線字串。

`init_db()`（`python -m pipeline.manage init-db`）保留給**全新的空資料庫**（測試、乾淨的本機環境）；它是 `create_all()`，只會補上「缺少的表」，**不會**改既有表的結構，所以不能拿來做 schema 演進。

### PoC 實作邊界（開源優先，對應規劃時的技術選型決策，MVP 階段陸續補上）
- LLM：Ollama + Qwen2.5-7B-Instruct（`pipeline/config.py` 可透過 `.env` 覆寫模型/host）
- TTS：Coqui XTTS-v2（zero-shot voice cloning，需一段參考語音 wav）
- 影片生成 I2V：**已接**（SVD／CogVideoX／Wan2.2，見上），只在明確指定 `animate_scenes`／`--animate` 時才用，預設仍是真實素材剪輯＋照片 Ken Burns（策略 A 優先，I2V 只補位）
- 圖像生成（背景）：**已接**（ComfyUI ＋ SDXL ＋ BiRefNet，見上），同樣只在明確指定 `background_scenes`／`--background` 時才用；生成的是寵物以外的部分，寵物像素最後會貼回去
- VLM：**已接**（`gemma3:12b` via Ollama），但目前只用在生成後的一致性檢查（見上），上傳素材的品質檢查仍然靠人工選片
- 音樂生成：仍刻意省略
- 任務編排：仍是同步流程，Celery/Temporal 留到規模需要時才導入；資料庫用 PostgreSQL（見上）
- Provider Adapter：LLM/TTS/I2V 都已有具體實作（`providers/llm/`、`providers/tts/`、`providers/video/`），但都還是「呼叫端寫死選哪個 provider」，還沒有 Router／依內容敏感度或成本自動切換，留到後續 MVP 切片

## 建議技術棧（規劃中，實作時以實際程式碼為準）
- 任務編排：Temporal 或 Celery
- 影片處理：FFmpeg + MoviePy（Python）
- 多代理框架：LangGraph 或 CrewAI
- 開源模型部署：vLLM（LLM/VLM）、ComfyUI（影像/影片 pipeline）
- 後端 API：Python（FastAPI）
- 人工審核 UI：React/Next.js
- 資料庫：PostgreSQL；快取/佇列：Redis；物件儲存：S3 相容(MinIO 或雲端)

## 開發規範
- 業務邏輯一律不得寫死單一 AI 廠商 SDK；一律經 Provider Adapter
- 涉及真實寵物照片/收容所個資的素材，預設走本地開源模型處理，除非該筆資料已標記可送外部 API
- 每個生成的影片都須保留「素材與生成紀錄」（用了哪些 provider、prompt、seed、模型版本、審核結果），供事後追溯與合規查核
- 人工審核層的退回機制須能標記「單一鏡頭重新生成」，不必整支影片重跑
- 腳本/分鏡一律走結構化 JSON（見 docs/schemas/），不要用純文字文案當作腳本的唯一表示
- 畫面上的創作決定要寫在腳本裡，不要只當成呼叫參數：背景怎麼處理是分鏡的一部分，寫進 script JSON 才會被 fact-check、QA、續跑、單鏡頭重生一起帶著走
- 專案已有 `pyproject.toml`（`pytest` ＋ `ruff`，可選 extras：`dev`／`web`／`i2v`），指令見下方「常用指令」；尚未有 `package.json`（前端目前是無建置流程的純 HTML/JS）

## Rules & Constraints

### 安全性（硬性規範，不可違背）
- **禁止把秘密寫進程式碼或版控**：API金鑰、資料庫連線字串、TTS/LLM provider token 一律走 `.env`（已被 `.gitignore` 排除）或環境變數，程式碼中只能出現 `os.environ` / `.env` 讀取邏輯，不得出現任何硬編碼金鑰、密碼、connection string
- **真實寵物/收容所個資預設走本地開源模型**（見上方「開發規範」），涉及送外部商用 API 的資料必須先確認該筆已標記可外送；不確定時視為不可外送
- **Provider Adapter 是唯一對外呼叫層**：任何新增的外部 API 呼叫都必須經過 `providers/` 下的 adapter，不得在 `pipeline/` 業務邏輯或 agent 程式碼中直接 import 特定廠商 SDK 或直接發 HTTP request 給外部服務
- **輸入驗證**：所有外部輸入（上傳的照片/影片/JSON、Pet Profile、使用者在審核介面填寫的文字）在進入 pipeline 前都要經過 schema 驗證（見 `docs/schemas/`），不得信任未驗證的輸入直接組字串下 shell 指令或 SQL
- **禁止字串拼接組 SQL**：資料庫查詢一律用參數化查詢（parameterized query）或 ORM，不得用 f-string/字串拼接組 SQL 語句
- **禁止字串拼接組 shell 指令**：呼叫 `ffmpeg`/`ffprobe` 等外部程式時，參數一律用 list 形式傳給 `subprocess`（`shell=False`），不得把使用者可控的檔名/文字直接嵌進 shell 字串
- **輸出內容不得外洩敏感資訊**：日誌、錯誤訊息、生成紀錄不得包含完整 API 金鑰、收容所內部聯絡方式以外的個資（例如飼主電話、地址全文）
- **MCP／外部工具存取需經使用者同意**：`.claude/mcp.json` 內的 postgres／github 等 server 屬範本，實際啟用前需確認連線字串/token 來源安全，且不得指向正式環境資料庫做未經確認的寫入操作

### 程式碼品質（硬性規範）
- **每個新功能／bug fix 都要有對應測試**（`tests/`，用 `pytest`），不接受無測試覆蓋的 pipeline 邏輯變更
- **合併前必須通過 `ruff check` 與 `ruff format --check`**（`.claude/hooks/post-edit.sh` 會在每次 Edit/Write 後自動跑 `ruff check --fix` + `ruff format`，但送出前仍需確認乾淨）
- **型別標註**：`pipeline/` 與 `providers/` 下的公開函式/方法一律加 type hints，資料結構優先用 `pydantic` model（對應 Pet Profile / Script schema），不用裸 `dict`
- **不寫死 magic number／字串**：字幕字級與行距（`config.SUBTITLE_FONT_SIZE`／`SUBTITLE_LINE_SPACING`——後者是**負值**，因為 drawtext 的預設是 0、鬆的是字型自己的行高：msjh 在 54px 下行距 144px 而字高只有 47px，兩行字幕看起來像兩句無關的話；而且畫面是**以文字區塊底部對齊**的，所以間距收合的速度是選項值的兩倍，這個數字是量出來的不是算出來的）、QA 加權評分門檻（80分）、5-7 鏡頭數量（`config.MIN_SCENES`／`MAX_SCENES`）、3-6 秒單鏡頭長度（`config.MIN_SCENE_DURATION`／`MAX_SCENE_DURATION`）、字幕字型（`config.DRAWTEXT_FONT_FILE`）、各 I2V provider 的模型檔名/步數/解析度（`config.SVD_*`／`COGVIDEOX_*`／`WAN_*`）、背景生成的模型/取樣/接縫參數（`config.BACKGROUND_*`）等關鍵參數一律集中在 `pipeline/config.py`（可用 `.env` 覆寫），不散落在各處程式碼
- **Provider Adapter 介面變更需保持向下相容**：輸入輸出 schema 不可隨意破壞既有呼叫端，新增能力優先用新方法/新欄位而非改變既有介面語意
- **錯誤處理只在系統邊界做**：外部 API 呼叫、檔案 I/O、使用者輸入解析需要 try/except 並記錄可追溯的錯誤上下文；內部函式之間的呼叫信任呼叫端已驗證過的資料，不重複防禦

## 常用指令

```bash
# 環境設定：這個專案有自己的 .venv（見下方「開發環境」），每次開新的終端機都要先啟用
source .venv/Scripts/activate   # Windows Git Bash；已建過就不用重跑 python -m venv
pip install -e ".[dev]"
cp .env.example .env   # 依需要調整 OLLAMA_MODEL / XTTS_MODEL_NAME / DATABASE_URL / WAN_*

# 確認 Ollama 模型已就緒（需先安裝並啟動 Ollama）
ollama pull qwen2.5:7b-instruct
ollama list

# 啟動 PostgreSQL（需先手動啟動 Docker Desktop）
docker compose up -d
python -m pipeline.manage init-db   # 只用於全新的空資料庫
alembic upgrade head                # 既有資料庫：套用尚未執行的 schema 變更

# 匯入寵物 Profile 到資料庫（storage/profiles/*.json 只是匯入格式，不再是 pipeline 讀取來源）
python -m pipeline.manage import-profile storage/profiles/<pet_id>.json
python -m pipeline.manage list-pets
python -m pipeline.manage show-pet <pet_id>   # 看 Profile + 生成歷程

# 測試
pytest
ruff check .

# 執行 pipeline（pet_id 需已匯入資料庫；素材與參考語音 wav 為選用）
python -m pipeline.run \
  --pet-id <pet_id> \
  --voice-sample storage/assets/<pet_id>/voice_ref.wav \
  --music-track storage/assets/<pet_id>/music.mp3 \
  --style cute \
  --duration 30
# 印出的 "Job id" 可用來做單鏡頭重生；或在 Claude Code 內用 /gen-video 自訂 slash command

# 續跑失敗的生成（沿用已完成的鏡頭，只補跑沒做完的；不吃任何參數，全部從 job 讀）
python -m pipeline.resume <job_id>

# 單鏡頭重生（不重跑 LLM，只 patch 指定鏡頭後重新渲染整支影片）
python -m pipeline.regenerate <job_id> <scene_id> \
  --subtitle "新的字幕" \
  --music-track storage/assets/<pet_id>/music.mp3   # voice-sample/music-track 需比照原本生成時再傳一次

# Image-to-Video（需要 CUDA GPU；torch 要裝對應 CUDA 版本的 wheel，不能只 pip install torch）
pip install torch --index-url https://download.pytorch.org/whl/cu128   # 依實際 GPU/CUDA 版本調整
pip install -e ".[i2v]"        # 只涵蓋 svd / cogvideox（diffusers 路線）
python -m pipeline.regenerate <job_id> <scene_id> --animate --video-provider svd   # 或 cogvideox

# Wan2.2（動作品質最好；走另一個常駐的 ComfyUI 伺服器，不吃本專案的 [i2v] extras）
# 先照 STARTUP.md 啟動 vendor/comfyui（它有自己的 .venv），再：
python -m pipeline.run --pet-id <pet_id> --animate-scenes 2,4 --video-provider wan --animate-prompt "貓輕輕搖尾巴、抬頭看鏡頭"
python -m pipeline.regenerate <job_id> <scene_id> --animate --video-provider wan --animate-prompt "狗狗歪頭看鏡頭"

# AI 背景生成：extend（只補畫面空白邊，保留真實背景）／replace（去背後整個場景重生）
# 需要同一台 ComfyUI ＋ SDXL checkpoint；replace 另需 BiRefNet 去背模型
# prompt 必須是英文（SDXL 的 CLIP 不懂中文）：extend 描述整張畫面，replace 只描述場景
python -m pipeline.run --pet-id <pet_id> --background-scenes 1,3 --background-prompt "a grey cat resting in a cosy living room, warm afternoon light, realistic photograph"
python -m pipeline.run --pet-id <pet_id> --background-scenes 1,3 --background-mode replace --background-prompt "green grass in a sunny park, blurred trees behind, bright daylight, realistic photograph"
python -m pipeline.regenerate <job_id> <scene_id> --background --background-mode replace --background-prompt "green grass in a sunny park, blurred trees behind, bright daylight, realistic photograph"

# 簡易 FastAPI + 前端（無建置流程的純 HTML/JS，不是最終目標的 React/Next.js）
pip install -e ".[web]"
uvicorn webapp.main:app --reload   # 開 http://localhost:8000（.venv 啟用後裸指令就會抓對環境）
```

### 開發環境（.venv，不要裝進全域 Python）
這台機器上還有另一個獨立的 Python 3.12 安裝，PATH 上排在前面。一開始這個專案曾經誤裝進 miniconda 的 **base 全域環境**，導致裸打 `uvicorn`／`pytest` 等指令會抓到錯的 Python、找不到 `psycopg` 等套件。已改成專案自己的 `.venv`（`python -m venv .venv`，套件都裝在裡面）解決——**每次開新終端機工作前一定要先 `source .venv/Scripts/activate`**，啟用後裸指令（`python`、`pip`、`pytest`、`ruff`、`uvicorn`）都會正確指向 `.venv` 而不是全域環境或那個 Python 3.12 安裝。`.venv/` 已在 `.gitignore` 排除。

素材放置慣例：`storage/assets/<pet_id>/`（原始照片/影片/語音樣本，對應 Profile 的 `media.assets[].url` 檔名；網頁上傳也是存到這裡，並透過唯讀的 `/media` mount 給前端顯示縮圖）、`storage/output/<pet_id>/gen_<token>/`（每次生成/重生獨立的輸出子資料夾，含三種風格腳本 JSON、各鏡頭 clip、最終影片）。這兩個資料夾內容都被 `.gitignore` 排除，不會進版控。`storage/profiles/*.json` 是匯入資料庫用的格式範本，實際運作時 pipeline 讀的是資料庫，不是這個資料夾。

`vendor/`（整個目錄都在 `.gitignore` 內，**不進版控、需要時各自另外 clone/安裝**）：
- `vendor/comfyui/`：Wan2.2 用的 ComfyUI 伺服器，**有自己的 `.venv`**，跟專案主要的 `.venv` 完全分開；啟動方式見 [STARTUP.md](STARTUP.md)。
- `vendor/wan2.2/`：Wan 官方推論 repo，早期評估時用過，**目前的 `wan_provider.py` 已不再呼叫它**，留著只作為對照參考。
因為 vendor 底下的 repo 各自帶自己的測試，`pyproject.toml` 用 `[tool.pytest.ini_options] testpaths = ["tests"]` 把 pytest 的搜集範圍限制在本專案的 `tests/`（ruff 不需要同樣處理，它預設會遵守 `.gitignore`）。
