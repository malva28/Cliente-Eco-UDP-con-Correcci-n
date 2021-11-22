# Tarea 2 - Cliente Eco UDP con Corrección

Tarea para el curso de Redes (CC4303), Universidad de Chile. 

Emisor de archivos por conexión UDP en Python, utilizando GoBackN 
para la retransmisión de paquetes perdidos o corruptos, con un servidor 
de eco como intermediario.

Implementado en Python 3 utilizando la librería jsockets.py del
profesor José Piquer.


## Correr el programa

Para ejecutar el programa, simplemente hay que ejecutar

```
python main.py [ruta_del_archivo] [bytes_por_paquete]
```

Donde `[ruta_del_archivo]` se refiere a la ruta donde está el archivo que se 
quiere enviar al servidor y `bytes_por_paquete` es la propuesta de tamaño en 
bytes que tendrá cada paquete enviado y recibido durante la transferencia.

## Configuración de IP y puerto

Para configurar la dirección IP del servidor con el cuál se hará el contacto 
y el puerto de comunicación, abrir el archivo `config.py`, y editar las 
variables respectivas.

## Generar archivo de prueba

También se pueden generar archivos ASCII de texto de algún tamaño específico 
en bytes para transferir. Correr en la consola:

```
python gen_file.py [bytes_archivo]
```

Con `[bytes_archivo]` siendo el tamaño requerido. El archivo será guardado en 
la ruta `transfer_files/paquetes_[bytes_archivo].txt`

## Archivos adicionales

El proyecto ya viene con algunos archivos de prueba usados para testear el programa, 
están en la carpeta `transfer_files`. También en la carpeta `results` se guardan 
estadísticas sobre tests hechos, con distintos archivos, servers y opciones de 
imprimido en consola. En la carpeta `received` se guardan los archivos transferidos y
recibidos de vuelta por el servidor de eco para el posterior testeo de la funcionalidad
del programa.

## Variables del test

El programa se ejecuta una vez corrido `test.run_tests()`. El comportamiento del test 
se puede modificar cambiando los parámetros de su inicialización. Estos son:

* `chunk_size`: Propuesta del tamaño del paquete (en bytes)
* `transfer_filename`: Ruta al archivo a enviar
* `n_reps`: Veces en las que un experimento se repite
* `record`: Activar/Desactivar la toma de estadísticas en un archivo cuando las repeticiones son mayores a 1
* `timeout`: Tiempo (en segundos) antes de que el socket se desconecte por inactividad
* `reply_timeout`: Tiempo (en segundos) antes de retransmitir paquetes que aún no reciben ACK
* `window_size`: Tamaño de la ventana de transmisión de paquetes
* `packet_buffer_size`: Tamaño del buffer de emisión de paquetes. Debe ser mayor a `window_size`
