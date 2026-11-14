# Teste de encadeamento do AquaCrop-OSPy e medição de custo

Item 5 do Apêndice B do plano. Usa só o clima de exemplo que vem com o pacote. Nenhum dado do projeto foi baixado.

Reproduzir, a partir de `aftercrop/`:

```text
.venv-engine/bin/python docs/spikes/ospy_chain_spike.py      # cerca de 4 min, grava ospy-chain-results.json
.venv-engine/bin/python -m pytest tests/test_engine_chain.py -q   # 10 testes, cerca de 3,5 s
```

## 1. Resumo

| Pergunta | Resposta |
|---|---|
| O aquacrop 3.1.0 roda no Python 3.13.1? | Sim, com numpy 2.5.4 e pandas 3.1.1, sem aviso. Nenhum recuo foi preciso |
| O encadeamento funciona? | Sim. A água passa exata na troca (diferença de 0,0 mm, `th` idêntico até 1e-9) e o resultado muda com o estado inicial e com a antecessora |
| Custo por safra | 0,13 a 0,26 s, média de 0,21 s por instância, ou 0,95 ms por dia simulado, já com inicialização |
| Custo por cadeia de 3 safras | 0,63 s (soja, milho 2ª, soja) e 0,64 s (soja, trigo, soja), 668 dias simulados |
| 220 cadeias de 3 safras | 2,3 min em um processo. Com 7 anos de aquecimento por cadeia (17 instâncias), 13 min |
| Risco de custo | Baixo. Um orçamento de 8 h de execução é dezenas de vezes maior que a projeção |
| Risco que sobra | Agronômico, não de custo: com clima de teste e Ksat da textura, a maioria das safras zera ou tem dossel morto. Ver seções 7 e 8 |

## 2. Ambiente

| Item | Valor |
|---|---|
| Máquina | Apple M2, MacBook Air (Mac14,2, sem ventoinha), 8 núcleos (4 de desempenho e 4 de eficiência), 8 GB |
| Sistema | macOS 14.5, arm64 |
| Python | 3.13.1, do sistema, em `aftercrop/.venv-engine` |
| Pacotes | aquacrop 3.1.0, numpy 2.5.4, pandas 3.1.1, pytest 9.2.0. Lista completa em `requirements-engine.lock` |

- O aquacrop 3.1.0 é Python puro. O numba foi retirado no pacote (comentários em `aquacrop/initialize/compute_variables.py:221` e `:228`, "removal of numba AOT/JIT compilation"). Não há compilação na primeira execução, e o aquecimento só carrega imports e caches.
- O wheel declara `Requires-Python >=3.9` e só pede `numpy>=1.22`, `pandas>=2.0`, `tqdm`, `matplotlib` e `seaborn`. Por isso nenhuma das alternativas previstas (fixar numpy, pandas ou numba, ou instalar `uv` com Python 3.11) foi usada.
- matplotlib e seaborn entram como dependência obrigatória do pacote, sem uso no motor. Não são ML nem cliente de LLM (M3.8).

## 3. O que foi escrito

| Arquivo | Papel |
|---|---|
| `pipeline/engine/soilmodel.py` | `DZ` comum de 14 compartimentos e 2,5 m. Um `Soil("custom", calc_cn=1, adj_rew=0)` novo por instância, camadas por textura e Ksat opcional por camada |
| `pipeline/engine/crops.py` | SoybeanGDD, MaizeGDD e WheatGDD. Sobrescritas só de marcos de ciclo, Zmax e `Tmax_lo`/`Tmax_up`. Zmax base 1,0 m. Chave desconhecida levanta erro e cada valor é lido de volta |
| `pipeline/engine/weather.py` | Clima de exemplo e checagem de colunas, NaN e série diária contínua |
| `pipeline/engine/chain.py` | Uma instância por safra. Só `th` passa. Início no dia seguinte à colheita, `off_season=True`, fim 300 dias após a semeadura, exigência de uma só estação por instância, geometria conferida depois da inicialização e conservação de até 1 mm na troca |
| `tests/test_engine_chain.py` | Geometria, sobrescritas, linha certa do estado, troca, sensibilidade ao estado e à antecessora, repetição por sha256, Ksat, ausência do padrão proibido |
| `docs/spikes/ospy_chain_spike.py` | Script deste teste. Grava `docs/spikes/ospy-chain-results.json` |

Clima de teste: `cordoba_climate.txt` do pacote, 1991 a 2021, 11.323 dias sem falha. É o único arquivo do hemisfério sul (janeiro com 17 °C de mínima e 32 °C de máxima). Chuva média de 867 mm por ano, com inverno seco (9 a 16 mm em junho a agosto) e frio (4,6 °C de mínima em agosto). Londrina é mais úmida e tem inverno mais quente, então os números agronômicos daqui não se transferem.

Solo de teste: textura de argila muito alta, inventada para o teste e não tirada do SoilGrids. Camada de 0 a 0,3 m com areia 15%, argila 65% e MO 2,5%. Camada de 0,3 a 2,5 m com areia 12%, argila 70% e MO 1,0%. A fórmula de textura do OSPy dá CC 0,471 e PMP 0,375 na superfície, com Ksat de 16,7 mm/dia (0,7 mm/h).

## 4. Encadeamento

Sequências na safra de teste 2001/02, início em 01/08/2001:

- Braço milho: soja semeada em 01/10/2001, milho 2ª semeado no dia seguinte à colheita da soja e soja semeada em 30/11/2002.
- Braço trigo: a mesma soja, trigo semeado em 01/05/2002 e a mesma soja em 30/11/2002.
- 30/11/2002 é a primeira data com 20 mm em 3 dias depois das duas colheitas, igual nos dois braços.

Partindo da capacidade de campo (1.245,7 mm no perfil):

| Braço | Safra | Período da instância | Armazenamento início → fim (mm) | Produtividade (t/ha) | Artefato |
|---|---|---|---|---|---|
| milho | soja | 01/08/2001 a 18/03/2002 | 1.245,7 → 1.183,2 | 0,46 | |
| milho | milho 2ª | 19/03/2002 a 17/07/2002 | 1.183,2 → 1.180,9 | 0,00 | dossel morto |
| milho | soja | 18/07/2002 a 30/05/2003 | 1.180,9 → 1.176,3 | 0,16 | |
| trigo | soja | 01/08/2001 a 18/03/2002 | 1.245,7 → 1.183,2 | 0,46 | |
| trigo | trigo | 19/03/2002 a 28/10/2002 | 1.183,2 → 1.198,5 | 0,00 | nunca teve dossel, mas sai "matured" |
| trigo | soja | 29/10/2002 a 30/05/2003 | 1.198,5 → 1.176,2 | 0,17 | |

Partindo de 25% da água disponível (1.062,4 mm), no braço milho: soja com 0,00 t/ha, milho morto em 26/08 e soja final com 0,375 t/ha, terminando em 1.046,0 mm. No braço trigo, a soja final dá 0,41 t/ha.

O que isso confirma:

- A troca conserva a água. Em todas as 8 trocas, o armazenamento lido pelo modelo depois da inicialização é igual ao deixado pela safra anterior (diferença de 0,0 mm, e o maior desvio entre os vetores `th` é 0,0).
- O estado inicial muda o resultado. A soja final termina com 130 mm a menos e muda de produtividade quando a cadeia parte seca.
- A antecessora muda o estado da soja seguinte. A soja começa com 1.180,9 mm depois do milho e 1.198,5 mm depois do trigo, e o prefixo comum é idêntico bit a bit nos dois braços.
- O sinal das produtividades não se interpreta: com milho morto e trigo sem dossel, a comparação é entre artefatos.

Armadilhas da ficha M3, agora conferidas na execução:

| Armadilha | Resultado |
|---|---|
| Linhas depois do último passo ficam zeradas | Confirmado. O estado é a última linha com `dap > 0`, que é o dia da colheita. `chain.last_planted_row` faz isso, e o teste cobre a cauda zerada |
| O modelo termina na colheita mesmo com `off_season=True` | Confirmado. O pousio seguinte abre a instância seguinte |
| A geometria muda se `Zmax + 0,1` passar da soma de `dz` | Com `DZ` de 2,5 m a geometria não muda nem com o Zmax padrão do milho (2,3 m). O código levanta erro se mudar |
| Graus-dia insuficientes abortam | É um `assert` em `compute_crop_calendar.py:260`. Com fim 300 dias depois da semeadura, não disparou em 29 anos de teste |
| `crop_list = [crop]` | Confirmado em `read_model_parameters.py`. Uma instância por safra, com exigência de `n_seasons == 1` |

Armadilhas novas, achadas agora:

1. `InitialWaterContent()` padrão preenche só a camada 1. As camadas abaixo começam com água zero, abaixo do ponto de murcha (141 mm no perfil em vez de 1.246 mm). O motor passa "FC" em todas as camadas.
2. `Crop(...)` ignora em silêncio qualquer kwarg fora da lista permitida. `crops.py` levanta erro antes.
3. O OSPy pode dar "matured" a uma safra que nunca teve dossel, porque a maturidade só conta graus-dia. O artefato tem de ler o dossel (`canopy_max`, `days_to_emergence`) e `crop_dead`, nunca só a maturidade.
4. O dossel pode morrer no dia em que emerge, com solo superficial seco. Medir a emergência só pelo dossel maior que zero devolve "nunca emergiu" nesse caso. `germinated` vem separado.
5. A rampa de calor da biblioteca está invertida, como o plano diz. O código usa `Tmax_lo` como início e `Tmax_up` como falha total (`solution/temperature_stress.py:61` a `:66`), e a biblioteca traz 45 e 40. `crops.py` aplica 40/45 em soja e milho e 35/40 em trigo.
6. O motor usa atributos privados do `AquaCropModel` (`_initialize`, `_clock_struct`, `_param_struct`, `_init_cond`) para ler o estado inicial e conferir a geometria antes de rodar. Com a versão fixada isso é estável. Mudar de versão exige rodar o teste de novo.
7. O 3.1.0 tem gatilho de semeadura por chuva (`PlantTrigger`, `PlantRainThr`, `PlantRainWindow`, `PlantMaxDelay`, em `timestep/adaptive_planting.py`), desligado por padrão. Poderia fazer a regra de 20 mm em 3 dias, mas o calendário de graus-dia é calculado na data nominal antes do deslocamento. Não foi usado, e `sowing.py` continua sendo o caminho.

## 5. Custo

Medido com o processo já aquecido, 7 repetições de cada cadeia, cada safra medida da construção do modelo até a extração do estado.

| Safra | Dias simulados | Média (s) | Desvio (s) |
|---|---|---|---|
| soja 1 (com 61 dias de pousio antes) | 230 | 0,248 a 0,257 | 0,002 a 0,015 |
| milho 2ª (morre em julho) | 121 | 0,132 | 0,002 |
| trigo | 224 | 0,194 | 0,001 |
| soja final, braço milho | 317 | 0,245 | 0,004 |
| soja final, braço trigo | 214 | 0,193 | 0,002 |

| Medida | Valor |
|---|---|
| Média por instância | 0,211 s, dos quais 0,020 s de inicialização |
| Por dia simulado | 0,95 ms |
| Cadeia soja, milho 2ª, soja (668 dias) | 0,625 ± 0,007 s |
| Cadeia soja, trigo, soja (668 dias) | 0,645 ± 0,015 s |
| Varredura de 29 anos, 145 instâncias, 26.248 dias | 23,5 s, ou 0,16 s por instância e 0,90 ms por dia |

O custo depende dos dias simulados, quase nada dos compartimentos:

| Geometria (2,5 m) | Cadeia de 3 safras (s) |
|---|---|
| 12 compartimentos | 0,609 |
| 14 compartimentos (`DZ`) | 0,621 |
| 25 compartimentos de 0,1 m | 0,654 |

Processos em paralelo (`ProcessPoolExecutor`, 48 cadeias, processos aquecidos antes de medir):

| Processos | Tempo (s) | Cadeias por s | Ganho | Tempo médio da cadeia no processo (s) |
|---|---|---|---|---|
| 1 | 30,2 | 1,59 | 1,00 | 0,63 |
| 2 | 16,5 | 2,91 | 1,83 | 0,69 |
| 4 | 16,1 | 2,98 | 1,88 | 1,33 |
| 6 | 13,4 | 3,59 | 2,26 | 1,65 |
| 8 | 11,2 | 4,29 | 2,70 | 1,83 |

Repetição logo depois, mesma carga de fundo: ganho de 1,76 com 2 processos, 2,28 com 4, 2,76 com 6 e 2,72 com 8. O teto prático nesta máquina ficou em cerca de 2,7 vezes, com 6 a 8 processos.

Custo frio, num processo novo: 0,47 s de import, 0,02 s para ler o clima, 0,64 s na primeira cadeia e 0,62 s na segunda. Sem numba, a primeira cadeia não paga compilação. Memória máxima do processo: 76 MB, então 8 processos cabem com folga em 8 GB.

O ganho fica abaixo do número de núcleos de desempenho por três motivos. Durante a medição, o Spotlight do macOS usava 1,5 a 2 núcleos (`spotlightknowledged`, `mds_stores`), provavelmente indexando as venvs novas, e o outro agente trabalhava na mesma máquina. Os 4 núcleos de eficiência do M2 são bem mais lentos em Python. E o MacBook Air não tem ventoinha. O número serve como piso, não como teto.

## 6. Projeção para as 220 cadeias e orçamento de execução

A bancada da seção 11.2 soma 220 cadeias: 20 da rodada base, 20 de A1 e 180 de P1. O comprimento da cadeia de ensaio, com o aquecimento, ainda não está fixado. A tabela usa a média de 0,211 s por instância medida acima e duas safras por ano.

| Instâncias por cadeia | Aquecimento | 1 processo | Ganho medido de 2,7 com 8 processos |
|---|---|---|---|
| 3 | nenhum | 2,3 min | 0,9 min |
| 5 | 1 ano | 3,9 min | 1,4 min |
| 9 | 3 anos | 7,0 min | 2,6 min |
| 17 | 7 anos | 13,2 min | 4,9 min |
| 84 | 42 anos, 1984 a 2025 inteiro | 65 min | 24 min |

Orçamento do plano (seção 16.3): 8 h de execução para a bancada inteira. A rodada só começa se `timing.json` mostrar que as cadeias cabem nesse orçamento. Mesmo o pior caso da tabela, cada uma das 220 cadeias percorrendo os 42 anos em um só processo, usa 65 min, cerca de 1/7 das 8 h. A conta deste documento para R1, se voltar com 50 membros, 3 talhões, 2 braços, 40 safras e 3 instâncias, dá cerca de 36.000 instâncias, ou 2,1 h em um processo e menos de 1 h com 8.

Ressalvas da projeção:

- O clima de teste não é o da POWER. O custo por dia não deve mudar muito, mas safras mais longas ou mais curtas mudam o custo por instância. O número que decide se a rodada cabe é o de `timing.json`, medido com o clima real.
- Ensaios e talhões compartilham o prefixo da cadeia (seção 10.5). Rodar o prefixo uma vez e ramificar dali corta quase metade do custo, mas não é preciso para caber.

## 7. Ksat

Pedido do coordenador: fontes conferidas por ele dão Ksat medido de 60,5 a 78 mm/h (1.450 a 1.870 mm/dia) em Latossolo muito argiloso do norte do Paraná, só na superfície. A fórmula de textura dá menos de 1 mm/h.

### 7.1 Onde o Ksat entra no aquacrop 3.1.0

Caminhos relativos a `.venv-engine/lib/python3.13/site-packages/aquacrop/`.

| Onde | Arquivo e linha | Efeito |
|---|---|---|
| Fórmula de textura | `entities/soil.py:296` e `:297` | `Ksat = 1930 * (th_s - th_fc) ** (3 - lambda) * 24`, em mm/dia. Dá 16,7 mm/dia no solo de teste |
| Drenagem `tau` | `entities/soil.py:359` a `:363`, em `add_layer` (`:319`) | `tau = 0,0866 * Ksat ** 0,35`, limitado a 1. Com 16,7 mm/dia, `tau` = 0,23. Acima de cerca de 1.085 mm/dia (45 mm/h), `tau` = 1 |
| Número de curva | `initialize/compute_variables.py:65` a `:76` | Com `calc_cn=1`, o CN sai do Ksat do primeiro compartimento só: acima de 864 mm/dia vale 46, acima de 347 vale 61, acima de 36 vale 72, e abaixo disso 77. O CN define o escoamento em `solution/rainfall_partition.py:82` a `:148` |
| Infiltração na superfície | `solution/infiltration.py:139` a `:144` (sem camalhão), `:110` a `:115` (com camalhão) | A água que entra num dia é limitada ao Ksat do primeiro compartimento, e o excesso vira escoamento |
| Redistribuição na infiltração | `solution/infiltration.py:167` a `:205` | `tau` e Ksat de cada compartimento limitam o fluxo para baixo |
| Drenagem diária | `solution/drainage.py:65` a `:82` e `:113` a `:115` (e repetições em `:172`, `:206`, `:250`, `:296`) | `tau` define a velocidade de drenagem acima da capacidade de campo, e o Ksat do compartimento limita o fluxo acumulado |
| Ascensão capilar | `entities/soil.py:385` em diante, `solution/capillary_rise.py` | Só com lençol freático, fora do motor |

No solo de teste, o Ksat da textura limita a infiltração a 16,7 mm por dia no primeiro compartimento, põe o CN no degrau mais alto (77) e o `tau` em 0,23. Qualquer Ksat de superfície acima de 864 mm/dia (36 mm/h) leva o CN a 46, e acima de cerca de 45 mm/h leva o `tau` a 1. Com 1.440 mm/dia, o teto de infiltração deixa de ser atingido por chuva diária. Por isso 60 e 78 mm/h dão resultado idêntico em tudo, dentro da precisão impressa.

### 7.2 Cadeia soja, milho 2ª, soja com os dois Ksat

Ksat medido só na camada de 0 a 0,3 m: 60 mm/h (1.440 mm/dia) e 78 mm/h (1.872 mm/dia). Abaixo de 0,3 m fica o Ksat da textura (16,1 mm/dia), sem inventar valor de subsuperfície. Textura, CC e PMP iguais nas duas versões.

Safra de teste 2001/02, partindo da capacidade de campo:

| Safra | Textura: armazenamento no fim (mm) | Textura: t/ha | Medido 60 ou 78: armazenamento no fim (mm) | Medido: t/ha |
|---|---|---|---|---|
| soja | 1.183,2 | 0,46 | 1.190,4 | 0,71 |
| milho 2ª | 1.180,9 | 0,00 (morto em 17/07) | 1.178,3 | 0,40 (morto em 12/10) |
| soja | 1.176,3 | 0,16 | 1.164,2 | 1,83 |

Média dos 29 anos do clima de teste (1991 a 2019), mesma regra de datas da seção 4:

| Safra | Medida | Textura | Medido 60 ou 78 |
|---|---|---|---|
| soja 1 | água passada (mm) | 1.177,9 | 1.186,9 |
| | produtividade (t/ha) | 0,84 | 1,72 |
| | escoamento (mm) | 131 | 14 |
| | percolação (mm) | 2 | 51 |
| | transpiração (mm) | 125 | 243 |
| | safras com falha | 8 de 29 | 4 de 29 |
| milho 2ª | água passada (mm) | 1.166,4 | 1.181,6 |
| | produtividade (t/ha) | 0,58 | 1,36 |
| | safras com falha | 26 de 29 | 22 de 29 |
| soja 2 | água passada (mm) | 1.165,3 | 1.181,9 |
| | produtividade (t/ha) | 0,16 | 0,96 |
| | escoamento (mm) | 84 | 10 |
| | safras com falha | 21 de 29 | 9 de 29 |

Falha é dossel morto, safra não madura ou produtividade zero. Esta varredura roda só o braço milho, e a data da soja 2 segue só a colheita do milho. Por isso a soja 2 com textura dá 21 falhas aqui e 22 na varredura com os dois braços da seção 8.

Qual caminho pesa mais, separado com o CN forçado (`calc_cn=0`), mesma varredura de 29 anos (`ksat_paths` no JSON):

| Caso | CN | Soja 1: t/ha | Soja 1: escoamento (mm) | Milho 2ª: t/ha | Soja 2: t/ha | Soja 2: água no fim (mm) |
|---|---|---|---|---|---|---|
| Ksat da textura, CN calculado | 77 | 0,84 | 131 | 0,58 | 0,16 | 1.165,3 |
| Ksat da textura, CN forçado | 46 | 0,88 | 126 | 0,70 | 0,25 | 1.166,3 |
| Ksat medido, CN forçado | 77 | 1,57 | 57 | 1,11 | 0,80 | 1.179,1 |
| Ksat medido, CN calculado | 46 | 1,72 | 14 | 1,36 | 0,96 | 1.181,9 |

Leitura:

- O efeito do Ksat medido passa sobretudo pelo teto de infiltração do primeiro compartimento (`infiltration.py:139` a `:144`), não pelo CN. Só trocar o CN quase não muda nada. Só trocar o Ksat, com CN 77, leva a maior parte do ganho. O CN 46 completa o resto.
- No total, o escoamento cai cerca de 90%, a percolação passa de 2 para 51 mm na soja 1, a transpiração dobra e a produtividade simulada sobe 2 a 6 vezes. A água passada entre safras sobe 9 a 17 mm em média.
- A escolha entre 60 e 78 mm/h não importa no OSPy, porque os dois caem no mesmo degrau de CN e no mesmo `tau`. O que importa é medido contra textura.
- A mudança é maior que qualquer contraste esperado entre antecessoras. Se o Ksat medido entrar, tem de entrar no protocolo antes de qualquer resultado (P1.7), e a versão de textura vira sensibilidade.
- O CN e o teto de infiltração usam só o Ksat do primeiro compartimento (0 a 0,1 m). Dar o valor medido a 0,1 m ou a 0,3 m dá o mesmo CN e o mesmo teto. Só a redistribuição e a drenagem mudam com a espessura escolhida.
- Clima de teste, solo inventado e Zmax de 1,0 m. A direção do efeito é do código, a magnitude não vale para Londrina.

## 8. Riscos

| Risco | Tamanho | O que fazer |
|---|---|---|
| Custo da cadeia passar do orçamento | Baixo. Projeção de 2 a 65 min para 220 cadeias contra 8 h | Medir de novo com a POWER e gravar em `timing.json` |
| A bancada produzir só artefatos | Alto neste teste: com Ksat da textura, falham 26 de 29 milhos, 27 de 29 trigos e 22 de 29 sojas seguintes em cada braço. T0 com soja zerada não passa, e T1 lê artefato | Antes da bancada, rodar a varredura de artefatos com a POWER de Londrina (cerca de 30 s) |
| Ksat decidido tarde | Alto. Muda a produtividade 2 a 6 vezes, sobretudo pelo teto de infiltração | Fixar no protocolo antes de qualquer resultado |
| Semeadura em solo seco | Confirmado: na safra de teste 2000/01, a soja seguinte morre na emergência nos dois braços, tanto em 15/12/2001 quanto em 21/12/2001, a primeira data com 20 mm em 3 dias. A camada de raiz inicial (0,3 m) está seca depois da antecessora, e dezembro tem 234 mm de ET0 contra 80 mm de chuva | A regra de chuva de `sowing.py` (seção 10.6) é necessária, mas não basta. A contagem de artefatos por braço decide se o Δ pode ser lido |
| Mac dormir durante a rodada | Médio. Um MacBook Air para tudo ao dormir | Rodar com `caffeinate -dims make -k ...`, na tomada |
| Paralelismo abaixo do esperado | Baixo, a projeção cabe em 1 processo | Usar até 4 processos e não depender do ganho |
| Versão do aquacrop mudar | Baixo com versão fixada. O motor usa atributos privados | Manter `aquacrop==3.1.0` e o lock |

O que este teste não cobre: clima POWER, solo do SoilGrids, regra de semeadura, `artifacts.json`, `timing.json` e as cadeias reais dos ensaios. Ficam fora deste teste.

## 9. Armadilhas achadas ao completar o M3

Só clima de exemplo do pacote e clima sintético. Nenhum arquivo de `data/raw/` foi aberto.

1. O OSPy lê o clima por posição. `core.py:241` converte a tabela em matriz, e o passo diário usa 0 = MinTemp, 1 = MaxTemp, 2 = Precipitation, 3 = ReferenceET e 4 = Date. O setter de `weather_df` só confere se os nomes existem. Uma tabela com outra ordem roda em silêncio com variáveis trocadas. `weather.check_weather` agora devolve sempre essa ordem.
2. `read_weather_inputs.py` levanta erro se o fim da simulação passar da última data do clima. Com 300 dias depois da semeadura, safras perto de 31/12/2025 cairiam nisso. `chain.run_season` corta o fim na última data, e o `assert` de graus-dia vira `CalendarError` (falha de infraestrutura, não desfecho do modelo).
3. `Soil.add_layer` atribui compartimentos pela profundidade de fundo. Uma camada mais fina que um compartimento (0 a 5 cm do SoilGrids) fica sem compartimento e quebra a camada seguinte. `soilmodel.texture_layers` faz a média ponderada das camadas por compartimento.
4. `prepare_weather` limita a ET0 a 0,1 mm. O conversor da POWER aplica o mesmo piso e lista os dias cortados.
5. O balanço por safra fecha em 1e-11 mm: chuva + irrigação + ascensão + `GwIn` - escoamento - percolação - `TileDrain` - evaporação - transpiração - variação do armazenamento - água na superfície. `SeasonResult.balance_residual_mm` guarda o resíduo.
6. `adj_rew=0` calcula o REW pela textura, e `adj_rew=1` usa o valor dado. A rodada lateral de REW 9 precisa de `adj_rew=1`.
7. Na cadeia de ensaio com o clima de teste, uma data publicada pode cair antes da colheita simulada da antecessora (soja de 10/11/2004 colhida em 04/05/2005, trigo publicado em 04/05/2005). O braço para com registro estruturado, e `artifacts.py` marca `sowing_before_previous_harvest`.
