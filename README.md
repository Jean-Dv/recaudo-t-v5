# RECAUDO-T — Parte 2

Implementación práctica de los escenarios S1 (seguridad), P1 (desempeño) y D1
(disponibilidad) para la variante V5, ola de fraudes.

El proyecto usa únicamente la biblioteca estándar de Python. Requiere Python
3.11 o superior; fue preparado con Python 3.14.

## Tácticas implementadas

- **S1:** autenticación desafío-respuesta con HMAC-SHA256, desafío de un solo
  uso y contador anti-repetición.
- **P1:** reducción de sobrecarga mediante lotes adaptativos de hasta 25
  validaciones y una espera máxima de 50 ms. El procesamiento sigue siendo
  secuencial; no implementa concurrencia.
- **D1:** redundancia pasiva. El respaldo no recibe tráfico hasta que una
  solicitud real detecta la caída del primario. No usa Ping/Echo ni redundancia
  activa.

Las claves, tarjetas y saldos del proyecto son datos ficticios reproducibles.

## Comandos

Levantar el sistema completo con un solo comando:

```bash
make run
```

Esto inicia el gateway en `http://127.0.0.1:8000`, el primario en el puerto
8001 y el respaldo pasivo en el 8002. `Ctrl+C` detiene todos los procesos.

Ejecutar las pruebas:

```bash
make test
```

Ejecutar una calibración corta de todos los experimentos:

```bash
make quick
```

Generar la evidencia completa:

```bash
make evidence
```

La ejecución completa realiza 1.000 ataques por variante para S1, 6.000
validaciones por variante para P1, una inyección de falla durante 200
solicitudes para D1 y 1.000 validaciones por variante para el compromiso entre
seguridad y desempeño.

Después de generar la evidencia, construir el informe y el PDF:

```bash
make report
```

## Evidencia

- `evidence/raw/`: cada observación individual en CSV y el evento de
  conmutación en JSONL.
- `evidence/results/`: métricas calculadas y criterios de aceptación en JSON.
- `docs/part-2-report.md`: informe generado a partir de los resultados.
- `docs/part-2-report.pdf`: entregable PDF.

Los percentiles se calculan con el método de rango más cercano sobre las
latencias crudas y todas las duraciones usan un reloj monotónico.

## Ejecución selectiva

```bash
python3 -m recaudot experiment security
python3 -m recaudot experiment performance
python3 -m recaudot experiment availability
python3 -m recaudot experiment tradeoff
```

Se puede añadir `--quick` a cualquiera de estos comandos.
