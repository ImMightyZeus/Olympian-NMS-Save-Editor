# Olympian NMS Save Editor — Preguntas frecuentes

[Read in English](FAQ.md) · [Volver al README](README.es.md)

### ¿Es seguro usarlo en mi partida principal?
Olympian está hecho para no perder tu progreso: nada se escribe hasta que pulsas **Guardar**, antes de cada escritura se respalda el slot y cada cambio se verifica antes y después de escribir. Aun así, editar partidas nunca está libre de riesgo: para cambios grandes, pruébalos primero en una copia (*Inicio → Copiar a otro slot*).

### ¿Cómo deshago un cambio?
- Antes de guardar: **Descartar cambios**, arriba.
- Después de guardar: **Resumen → Historial** muestra los últimos cambios con "volver a antes de este cambio", o restaura cualquier respaldo desde **Inicio** (*Restaurar en…*). Restaurar también devuelve el estado del juego de ese momento, así que lo jugado después se pierde.

### ¿Por qué me pide cerrar el juego?
El juego tiene la partida en memoria y la sobrescribe al volver a guardar, así que los cambios hechos con el juego abierto se perderían o se mezclarían. Olympian comprueba si `NMS.exe` está abierto y no escribe si lo está.

### Uso Steam Cloud. ¿Algo especial?
Después de guardar con Olympian, abre el juego primero en **este** PC para que Steam suba la versión nueva. Si juegas primero en otro PC, Steam puede traer la partida vieja.

### ¿Dónde están mis respaldos?
En tu carpeta de partidas: `_OlympianBackups` (automáticos, uno antes de cada guardado, más `history.json`) y `_OlympianRestorePoints` (los que creas tú). Olympian nunca los borra; puedes quitar los viejos tú mismo.

### Usé una versión anterior y tenía las carpetas `_ZeusRespaldos` / `_ZeusPuntos`.
Se mueven solas a los nombres nuevos la primera vez que abres esa carpeta de partidas. No se pierde nada; si una carpeta está en uso, se vuelve a intentar la próxima vez.

### SmartScreen o mi antivirus avisan sobre el `.bat`.
El lanzador no está firmado. *Más información → Ejecutar de todas formas*. Puedes leer `run_olympian_nmsse.bat`: solo busca o instala Python, prepara un entorno local en `.venv` y abre la app. La app solo escucha en `127.0.0.1` y no usa internet.

### ¿Tengo que instalar Python?
No. Si no encuentra un Python adecuado, el lanzador instala Python 3.13 oficial para tu usuario con `winget` (Windows puede pedirte permiso). Si un Python 3.13 que ya tienes está dañado, te dice cómo repararlo.

### El juego se actualizó. ¿Olympian se rompe?
Olympian lee el formato de la partida de forma general, así que la mayoría de las actualizaciones no lo afectan. Los datos del juego (nombres de objetos, límites de pila, misiones…) pueden quedar desactualizados: **Ajustes** lo indica. Los objetos nuevos aparecen entonces por su ID hasta regenerar los datos con `tools/build_gamedata.py`.

### ¿Por qué no puedo cambiar la clase de una nave, las estadísticas de un asentamiento o terminar una construcción?
Se dejaron fuera a propósito. Cambiar la clase no recalcula las estadísticas de la nave como lo hace el juego, y editar estadísticas o saltarse construcciones quita justo la parte del juego de la que tratan esos sistemas. Olympian se queda en cambios que mantienen tu partida coherente.

### ¿Por qué Olympian no recoge la producción de los asentamientos?
La partida no guarda una cantidad acumulada (en partidas reales vale 0); el juego la calcula con sus propios temporizadores cuando hablas con el supervisor. Recogerla en el editor sería inventar cantidades, así que Olympian muestra qué produce cada asentamiento y deja que la recojas en el juego.

### ¿Qué hace "Descartar decisión" en Asentamientos?
Quita la decisión pendiente sin aplicar ninguna opción ni recompensa. El juego trae la siguiente después de su espera habitual (15 a 120 minutos, según los datos del juego). Resolverla en el juego es lo que da sus recompensas.

### Hay tecnología que no se puede desinstalar.
La tecnología básica (soporte vital, protección, jetpack, hiperimpulsor…) tampoco se puede desmontar en el juego, así que Olympian no lo permite.

### ¿Qué es el modo experto del visor JSON?
Una forma de cambiar a mano un solo texto, número o sí/no. Mantiene el tipo del valor, nunca agrega ni quita campos, cambia solo el archivo que estás viendo y pasa por la misma verificación que cualquier otro cambio. Úsalo solo si sabes qué significa el valor.

### ¿Por qué algunos objetos muestran un ID en vez de un nombre?
Son objetos que los datos actuales no conocen (normalmente de una versión más nueva del juego) u objetos procedurales cuyo nombre genera el juego al jugar. Igual se mueven y se conservan bien.

### ¿Funciona con partidas de Xbox / Game Pass, PlayStation o Switch?
No. Solo con el formato de PC (Steam / GOG).

### ¿Me pueden banear por editar mi partida?
Olympian solo edita archivos locales. No se conocen baneos en No Man's Sky por editar partidas, pero en multijugador los demás ven lo que llevas, así que usa el sentido común.

### Algo salió mal. ¿Qué envío?
Abre un [issue](../../issues) con el contenido de `olympian_log.txt` (junto al `.bat`) y qué estabas haciendo. Si una partida no carga, primero restaura su respaldo desde Inicio.

### ¿Cómo puedo apoyar Olympian?
Es gratis y lo seguirá siendo. Las donaciones son bienvenidas en [paypal.me/ImMightyZeus](https://paypal.me/ImMightyZeus); reportar errores y compartir el proyecto también ayuda mucho.

### ¿Quién hizo Olympian?
MightyZeus, de Nicaragua 🇳🇮. Mira los créditos en el [README](README.es.md#créditos).
