# Guía de grabación y gestos

Amplía [GUION-GRABACION.md](GUION-GRABACION.md) (los dos clips principales)
con el material para reuniones largas y los gestos. Todo se graba desde la
pestaña **Grabar**; el audio no se graba nunca.

## 1. Material

| Material | Obligatorio | Duración | Cómo se usa |
|---|---|---|---|
| Clip EN SILENCIO (principal) | **Sí** | 15 s (8-25) | Referencia de encuadre y color de todos los demás |
| Clip HABLANDO (principal) | **Sí** | 15 s (8-25) | Habla |
| Silencio LARGO | Recomendado (1-3) | 90 s (30-180) | Grupo de silencios extra: evita que el bucle se reconozca en 1-2 h |
| Habla LARGA | Recomendado (1-2) | 60 s (20-120) | Grupo de hablas extra: más bocas distintas para seguir la voz |
| Gestos (8) | Opcional, cada uno | 3 s (2-6) | Saludar, Sonreír, Asentir, Negar, Mirar arriba/izquierda/derecha, Reír |

Mínimo para funcionar: los dos principales. Para reuniones de 1-2 horas con
saludos: añade al menos un silencio largo, una habla larga, Saludar, Sonreír
y Asentir.

## 2. Condiciones comunes (las que hacen que los clips enlacen)

- **Misma sesión, mismo sitio, misma ropa, misma luz.** Luz frontal difusa;
  evita ventanas detrás o luz que cambie (nubes).
- **Cámara fija** (no en el portátil si vas a teclear). Exposición y foco
  fijos si la webcam lo permite; con poca luz la webcam baja los FPS y la
  aplicación avisa de los FPS reales al terminar.
- **Pose de referencia**: la primera imagen del clip en silencio principal.
  En la vista previa se superpone semitransparente («Mostrar la pose de
  referencia»); colócate hasta que coincida. Solo se ve en la vista previa,
  no se graba.
- **Empieza y termina cada clip en esa pose**, con la boca cerrada y mirando
  al objetivo. El guion en pantalla lo marca en ámbar (instantes críticos).
- Manos fuera del plano salvo en Saludar. Nada de tocarse la cara.

## 3. Procedimiento

1. Pestaña **Grabar** → *Buscar* → elige la cámara física.
2. Graba primero **Clip EN SILENCIO (principal)** y acepta usarlo: pasa a
   ser la referencia y aparece la silueta en la vista previa.
3. Graba **Clip HABLANDO (principal)**.
4. Graba los extra: en *Clip* elige «Silencio LARGO» o «Habla LARGA»; la
   duración se ajusta sola a su rango. Al aceptar se añaden a los grupos de
   la pestaña Videos.
5. Graba cada gesto («Gesto: Saludar», …). Al aceptar se asigna al gesto en
   la pestaña Gestos con su atajo por defecto.
6. Pestaña **Videos** → *Preparar*. Se normaliza, se crea la caché y el
   índice. Revisa los avisos (por ejemplo, un gesto que no empieza o no
   termina en pose neutra).
7. Desde la consola, equivalente: `camvoice index` y `camvoice gestures`.

## 4. Gestos durante la reunión

- **Teclado**: atajos globales, funcionan con la ventana de la reunión en
  primer plano. Por defecto:

  | Gesto | Atajo |
  |---|---|
  | Saludar | Ctrl+Alt+F1 |
  | Sonreír | Ctrl+Alt+F2 |
  | Asentir | Ctrl+Alt+F3 |
  | Negar | Ctrl+Alt+F4 |
  | Mirar arriba | Ctrl+Alt+F5 |
  | Mirar a la izquierda | Ctrl+Alt+F6 |
  | Mirar a la derecha | Ctrl+Alt+F7 |
  | Reír | Ctrl+Alt+F8 |
  | Pausar / reanudar video | Ctrl+Alt+F11 |
  | Cancelar gesto | Ctrl+Alt+F12 |

  Se editan en la pestaña Gestos («Guardar atajos»). Se rechazan
  combinaciones que romperían la escritura (AltGr) o que usa Teams. Si otra
  aplicación tiene ya una combinación, se indica cuál y el resto sigue
  funcionando. «Atajos de teclado activos» (pestaña Gestos o bandeja) los
  suspende al instante.
- **Ratón**: botones de la pestaña Gestos o menú **Gestos** del icono de la
  bandeja.
- Un gesto pedido mientras ya hay otro sustituye al pendiente; repetir el
  mismo gesto antes de `cooldown` se ignora. Al terminar vuelve a hablar o a
  silencio según tu voz en ese momento.
- **Gestos mientras hablas**: puedes grabar una variante hablando (por
  ejemplo, saludar mientras dices «hola, ¿qué tal?») y asignarla con
  `camvoice gestures --set saludar_hablando clip.mp4`. Usa el mismo atajo que
  el gesto base: si lo pides hablando se usa la variante. Sin variante, el
  gesto espera como mucho 170 ms a una micro-pausa o a un ancla, empieza con
  un fundido corto y al terminar vuelve al habla.
- **Pausar video** (`Ctrl+Alt+F11`, botón de la pestaña Gestos o bandeja):
  la cámara repite la última imagen hasta que reanudes. La ventana muestra
  «⏸ VÍDEO EN PAUSA» y la bandeja lo indica; la imagen emitida no lleva
  marcas. Mientras dura no se aceptan gestos. Al reanudar vuelve con un
  fundido corto.

## 4bis. Las 62 grabaciones numeradas

Si grabaste las escenas numeradas con la app Cámara (`Imágenes\Camera
Roll\N.mp4`), impórtalas desde la pestaña **Biblioteca** (o
`camvoice library --import` + `camvoice index`). Avisos frecuentes y qué
hacer:

- **«Los clips son 1.78:1 y la salida 1.33:1»**: la cámara graba 16:9 y la
  salida es 4:3; con el ajuste «cover» se recortan los laterales. Deja
  margen a los lados (manos y gestos dentro del 75 % central) o usa
  `fit: contain` (con bandas).
- **«N gestos no empiezan o no terminan en la pose neutra»**: en la primera
  importación real fueron 19 (acercarse a la pantalla, acomodar el cabello o
  la camisa, ajustar gafas, apoyar el mentón, asentir, cruzar los brazos,
  echarse hacia atrás, escribir una nota, inclinar la cabeza, inclinarse,
  mirar a otra pantalla, moverse a izquierda/derecha, pedir la palabra,
  pensar con la mano en el mentón, respirar, tocarse la cara y la oreja).
  Funcionan, pero entran y salen con un fundido más largo. Para que enlacen
  limpio, regrábalos empezando y terminando 1 s quieto en la pose de
  referencia.
- **Cadencia variable**: normal en la app Cámara (28-30 FPS); se remuestrea.
  Si hay tirones reales se avisa, pero el clip se usa igual.
- **Hablar → callar con fundido de reserva**: el clip de habla necesita
  momentos con la boca cerrada y la cabeza en la pose neutra para que el
  paso a silencio sea limpio. Al grabar el clip de habla, haz pausas breves
  (respirar, boca cerrada) cada pocas frases.

Los ensayos de la pestaña Biblioteca muestran, para cada cambio, qué par de
fotogramas se eligió, cuánto se esperó y si hubo fundido de reserva: úsalos
para decidir qué regrabar.

## 5. Validación del material

- `camvoice probe clip.mp4`: resolución, FPS, duración.
- `camvoice gestures`: qué gestos tienen clip, atajos y problemas.
- `camvoice index`: tras preparar muestra `d_ok` por clip (cuanto menor,
  más estable), el rango de apertura de boca y avisos.
- `camvoice soak --simulate --duration 7200`: simula 2 horas con voz
  sintética, gestos y una pausa manual cada 10 min, sin micrófono ni cámara,
  e informa de transiciones (en ancla / fundido corto), discontinuidades,
  gestos atascados, pausas y la imagen congelada más larga.
