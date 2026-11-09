# Quiniela Predictor

CLI en Python 3.10+ sin dependencias externas. Estima 1/X/2, genera dos columnas y un Pleno compartido por 1,50 €, y liquida contra el escrutinio de SELAE.

```powershell
python PredictorQuiniela.py predict data/jornada21.json --as-of 2026-11-09T20:00:00+01:00
python PredictorQuiniela.py import-results 2526 SP1 data/sp1.csv
python PredictorQuiniela.py import-results 2526 SP2 data/sp2.csv
python PredictorQuiniela.py predict data/jornada21.json --as-of 2026-11-09T20:00:00+01:00 --results data/sp2.csv
python PredictorQuiniela.py settle boleto.json jornada_liquidada.json
python PredictorQuiniela.py backtest historico.json --results resultados_verificados.csv
```

Añade `--csv archivo.csv` al importador para leer el CSV original sin acceso de red. Repite `--results` para cargar más archivos. Para liquidar, guarda el objeto de la estrategia elegida dentro de `tickets` como `boleto.json`.

## Datos

- Cada jornada JSON tiene `id`, `closes_at` y 15 `matches` en el orden del boleto. Cada partido lleva `competition`, `home`, `away` y opcionalmente `date`, `kickoff_at`, `market` y `popularity`. Usa ISO 8601 con desplazamiento horario para instantes.
- Cada elemento de `market` lleva `source`, `captured_at` y `odds` decimales o `probabilities` en orden 1/X/2. La hora debe ser la de una captura documentada o una publicación fechada de los valores exactos. Las cuotas se convierten a inversos normalizados. Los porcentajes LAE y de usuarios se guardan por separado en `popularity` con `kind`, `percentages`, `source` y `captured_at`.
- El Pleno puede llevar `goal_market` con dos marginales `probabilities_012M` en orden local y visitante, más fuente y hora. Se elige la moda de cada marginal. También se acepta `pleno_pick` con `score` de dos caracteres (por ejemplo `1M`), fuente y hora. Sin estas entradas se intenta usar Poisson. Los pronósticos manuales deben haber quedado registrados antes del cierre.
- CSV de resultados con `date,competition,home,away,home_goals,away_goals,available_at,source,availability_kind`. Marca `verified` solo si puedes acreditar la disponibilidad del resultado a esa hora. Los resultados de Football-Data reciben una disponibilidad estimada de dos días desde la fecha y sirven para la predicción exploratoria. La prueba cronológica los excluye. Sus cuotas de apertura y cierre carecen de una hora de captura por partido demostrada frente al cierre de la Quiniela, por lo que el importador solo usa resultados.
- Competiciones `SP1` y `SP2` para Primera y Segunda. Para Liga F se acepta CSV normalizado con `LIGA_F`. No hay importador automático de Liga F. Los alias explícitos se mantienen en `fuentes.py` y se aplican dentro de la competición.
- `official` contiene `source` con la URL del escrutinio SELAE, `signs` como cadena de 14 signos, `pleno` como dos caracteres y `payouts` en euros brutos por apuesta, con claves `10` a `15`. Introduce cero cuando el escrutinio confirme que la categoría no paga. El Pleno se suma a los 14 aciertos. Se usan signos oficiales, incluidos los casos en que SELAE resuelve el partido por un procedimiento especial.

## Comparación a igual coste

`market_top` juega el favorito de mercado y la siguiente columna más probable según el mercado. `blend_top` aplica lo mismo a la mezcla. `blend_coverage` conserva la primera columna de la mezcla y selecciona, entre las 127 siguientes más probables, la que maximiza la probabilidad conjunta de que al menos una alcance 10 aciertos. El cálculo es exacto dentro de la hipótesis de independencia y del conjunto de candidatos. La cobertura es una estimación del modelo, no una garantía ni un cálculo de rentabilidad. El Pleno se mantiene fijo en las tres estrategias para aislar la selección de los 14 signos.

El Poisson combina goles recientes en casa y fuera con un prior fijo. Exige cinco partidos válidos por rol y usa un máximo de doce. Cuando hay datos de ambos métodos aplica 65 % mercado y 35 % Poisson. Son pesos fijos sin calibrar. Si falta uno usa el disponible. Un margen de hasta 10 puntos porcentuales entre los dos primeros signos marca un partido dudoso. Si faltan probabilidades de algún partido o falta el Pleno, la CLI devuelve el diagnóstico sin boleto.

El backtest recibe un array JSON de jornadas. Usa solo fuentes fechadas antes del cierre y resultados con disponibilidad verificada. Excluye para todas las estrategias las jornadas sin mercado completo, Pleno o escrutinio. Reporta Brier multiclase, log loss, acierto del favorito, calibración por signo, premios brutos, balance, ROI y peor racha con premio inferior al coste. El retorno aplica premios oficiales publicados como si fueran fijos. Si una categoría tuvo cero ganadores, un boleto hipotético ganador podría haber cambiado el reparto. Tampoco se modelan impuestos.

No se incluye todavía un histórico de jornadas emparejado con capturas previas verificadas. La jornada 21 es una fotografía de Losilla del 9 de noviembre de 2026. LAE figura a las 07:24 y mercado a las 19:57, hora peninsular. No es una captura de cierre ni un backtest. Las competiciones dudosas se marcan `UNVERIFIED` para evitar cruces de equipos. Para medir un cambio de pesos, elige primero un periodo de calibración y deja un bloque posterior intacto para la prueba.

## Fuentes

- [Football-Data, España](https://football-data.co.uk/spainm.php)
- [Losilla, porcentajes](https://www.eduardolosilla.es/ayudas/porcentajes)
- [SELAE, reglas y formato](https://www.loteriasyapuestas.es/es/centro-de-ayuda/como-se-juega/como-jugar-a-la-quiniela)

Los históricos de The Odds API y Betfair pueden exigir acceso de pago. La CLI no requiere suscripción ni compra datos.
