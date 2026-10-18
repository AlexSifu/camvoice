# Guion de grabación

Cómo grabar los dos clips para que el resultado sea lo mejor posible. **La
calidad depende más de estos dos minutos de grabación que de cualquier ajuste
del programa.**

> La aplicación trae un **grabador con temporizador** que muestra estas señales
> en pantalla y para solo: pestaña **Grabar**. Este documento es la referencia;
> el grabador es la versión práctica.
>
> El texto no sale de golpe: aparece **una frase cada vez**, con el tiempo
> repartido según su longitud, y debajo se ve atenuada la frase siguiente para
> poder anticiparla. La cuenta atrás es configurable de 0 a 15 s.

---

## 1. Antes de grabar

| | |
|---|---|
| **Graba los dos seguidos** | Sin mover la cámara, sin cambiar la luz, sin levantarte, sin cambiarte de ropa |
| **Encuadre** | Cabeza y hombros. Los ojos a un tercio de la altura desde arriba |
| **Luz** | Frontal y constante. Nunca una ventana detrás |
| **Fondo** | **Quieto**. Sin reloj de pared, sin ventana a la calle, sin pantallas encendidas |
| **Cámara** | 30 FPS o más, y al menos la resolución de salida |
| **Audio** | Irrelevante: la aplicación **no usa** el audio de los clips |

### El fondo es donde más gente falla

Un **reloj de pared** en el fondo marcará siempre la misma hora, repitiéndose en
bucle. Una **ventana a la calle** tendrá coches que pasan y vuelven. Una
**pantalla encendida** cambiará de contenido y luego se repetirá. Cualquiera de
las tres delata el truco en segundos, por bueno que sea el resto.

---

## 2. Clip HABLANDO — 15 s

```
 0.0 ─ 0.6 s   BOCA CERRADA, mirando al objetivo, quieto
 0.6 ─ 13.5 s  Lee el texto en voz alta, a ritmo normal
13.5 ─ 15.0 s  Termina la frase y CIERRA LA BOCA
```

### Por qué el medio segundo del principio no es negociable

Tras un silencio largo, la aplicación **rebobina el clip de habla al primer
fotograma** (`resume_window_ms`, 1,5 s por defecto). Ese fotograma es la pose
desde la que arranca el habla cada vez.

Y el fundido cruzado dura 160 ms, unos 2,4 fotogramas a 15 FPS. Así que los
**tres primeros fotogramas** son los que se ven mientras aparece la imagen. Si
en ellos tienes la boca a medio abrir, cada vez que empieces a hablar la cara
aparecerá con una mueca.

Con boca cerrada al principio y apertura natural después, el arranque se ve
exactamente como empezar a hablar de verdad.

### Qué texto leer

En el grabador el texto va saliendo por frases, con su tiempo de lectura
repartido. Para un clip de 15 s quedan cuatro frases de entre 2,5 y 4,1 s: más
que suficiente para leer cada una mirando al objetivo sin ir a trompicones.

El contenido es irrelevante **salvo en dos cosas**: la distribución de sonidos y
que no se pueda leer en los labios.

Este texto tiene proporciones de vocales y de cierres de labios parecidas a las
del español hablado normal, y no dice nada que importe si alguien lo lee en los
labios:

> «Esta mañana el tiempo parece bastante bueno, aunque por la tarde puede
> cambiar. Me faltan algunos papeles para el informe, pero podemos verlo con
> calma y confirmar la fecha final.»

Son 30 palabras, unos 12 s a ritmo normal. Para un clip de 20 s, añade:

> «Si te parece, lo dejamos preparado y mañana lo revisamos juntos sin prisa.»

**Por qué funciona.** No hay sincronización labial: el clip se reproduce cuando
se detecta voz, sin relación con tus palabras. Entonces lo único que se puede
optimizar es que la **mezcla de formas de boca** se parezca a la del español
medio. Si grabas diciendo muchas «o» y «u», tu boca estará redondeada casi todo
el tiempo y chocará contra un habla real llena de «a» y «e».

| Sonido | En el español hablado | En el texto |
|---|---|---|
| `a`, `e` (boca abierta y estirada) | ~25 % | alto |
| `o`, `u` (boca redondeada) | ~11 % | moderado |
| `p`, `b`, `m` (labios cerrados) | ~8 % | **p**arece, **b**astante, **p**uede, **M**e, **p**apeles, **p**ero, **p**odemos, **m**añana |
| `f` (labio contra dientes) | ~1 % | **f**altan, con**f**irmar, **f**echa, **f**inal |

### Qué NO hacer en el clip de hablando

| No hagas | Por qué |
|---|---|
| **Contar números** | El ritmo «uno, dos, tres» es inconfundible al repetirse |
| **Exagerar la articulación** | Se ve teatral, y el desajuste con tu voz real canta más |
| **Hablar entre dientes** | La boca casi no se mueve y no parece que hables |
| **Sonreír y mantener la sonrisa** | Una sonrisa congelada en bucle es lo más delatador que existe |
| **Gesticular con las manos** | Una mano entrando y saliendo del plano, en bucle, es inmediata |
| **Mover la cabeza mucho** | La transición de 160 ms no puede absorber un cambio de postura |
| **Mirar a otro lado** | La mirada fija al objetivo es lo que sostiene la ilusión |

---

## 3. Clip EN SILENCIO — 15 s

Este es el que más tiempo está en pantalla. Si hay que cuidar uno, es este.

```
 0.0 ─ 0.6 s   BOCA CERRADA, mirada al frente
 0.6 ─ 15.0 s  Escucha. Parpadea con naturalidad. Respira. Nada más.
```

### El detalle que nadie espera: se reproduce hacia atrás

El modo de bucle por defecto es **ida y vuelta** (*pingpong*): al llegar al
final el clip se reproduce en reversa. Es lo que elimina el corte visible del
salto del último al primer fotograma.

La consecuencia: **la mitad del tiempo tus movimientos se ven al revés.**

| Se ve bien en reversa | Se ve mal en reversa |
|---|---|
| Parpadear | Rascarse |
| Respirar | Beber agua |
| Micro-movimientos de cabeza | Colocarse el pelo o las gafas |
| Asentir despacio | Girarse a mirar algo |
| Un gesto leve de atención | Tragar saliva de forma visible |
| | Tocarse la cara |

Si no quieres pensar en esto, cambia el bucle a **normal** en la pestaña
Videos; pero entonces el clip tiene que empezar y acabar en la misma pose, que
es más difícil de clavar.

### Qué hacer exactamente

- **Escucha de verdad.** Pon atención a algo real —un video, alguien hablando—
  y tu cara hará sola lo que tiene que hacer.
- **Parpadea normal.** Entre 15 y 20 veces por minuto. No lo cuentes.
- **Boca cerrada**, mandíbula relajada, sin apretar los labios.
- **Quieto pero vivo.** Cero quietud absoluta parece un fotograma congelado;
  moverse parece inquietud.

### Qué NO hacer

| No hagas | Por qué |
|---|---|
| Quedarte totalmente inmóvil | Se ve como imagen congelada, que es justo lo que se quiere evitar |
| Mirar al móvil o al teclado | Delata que no estás atendiendo |
| Bostezar, estirarte, suspirar visible | Irrepetible y muy llamativo en bucle |
| Sonreír de forma fija | Igual que en el otro clip |
| Dejar que alguien pase por detrás | El bucle lo repetirá cada 15 s |

---

## 4. Duración: por qué 8-20 s

| Duración | Resultado |
|---|---|
| Menos de 8 s | El bucle se nota. Se reconoce el patrón enseguida |
| **8-20 s** | **El punto correcto** |
| Más de 20 s | No aporta variedad y la caché crece: ~7 MB por segundo a 640×480 |

Dos clips de 15 s ocupan unos **210 MB** de caché de fotogramas. Es el precio de
no decodificar video en el bucle de salida.

---

## 5. Lista de comprobación final

Antes de dar los clips por buenos:

- [ ] Los dos grabados seguidos, misma luz, misma ropa, misma posición
- [ ] Primer fotograma de **ambos**: boca cerrada, mirada al objetivo
- [ ] Fondo quieto: sin relojes, sin ventanas a la calle, sin pantallas
- [ ] Ninguna mano ha entrado en el plano
- [ ] Nadie ha pasado por detrás
- [ ] Sin filtros de belleza ni fondo virtual de la cámara (parpadean)
- [ ] Cabeza en la misma posición en los dos clips
- [ ] En el clip de silencio no hay ningún gesto que se vea raro al revés
- [ ] 8 a 20 s cada uno

Comprobación automática de lo medible:

```
camvoice-cli probe hablando.mp4 silencio.mp4
```

Avisa si las resoluciones no coinciden, si la relación de aspecto no cuadra con
la salida o si algún archivo no se puede abrir.

---

## 6. Después de grabar

1. Pestaña **Videos** → elige los dos clips → **Iniciar**.
2. Mira la vista previa dos minutos hablando y callando. Busca: fotogramas
   negros, saltos en el bucle, la boca abierta al reanudar.
3. Si el bucle se nota, el clip es corto o te moviste demasiado.
4. Si la luz del clip no cuadra con tu sala actual, ajústalo en **Efectos** con
   brillo y temperatura en vez de volver a grabar.
5. Un poco de **grano** (10-20 %) ayuda: un clip comprimido se ve demasiado
   limpio para ser una webcam real.
