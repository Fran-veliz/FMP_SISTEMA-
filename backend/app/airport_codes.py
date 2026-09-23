"""Conversión IATA -> OACI para los aeródromos de origen/destino.

El itinerario completo (temporada, formato 'W25') que entrega DGAC usa
códigos IATA en la columna ORIGEN/DESTINO, pero el resto del sistema
(FormatoFMP, VLOOKUP de itinerario diario) trabaja en OACI. Esta tabla se
siembra en la base de datos (tabla `airport_codes`) para poder corregir o
agregar destinos nuevos sin tocar código.

Fuente: cruzado contra una base pública de aeropuertos (IATA/OACI) y
validado contra los códigos OACI ya presentes en el Excel original
(ej. CIX->SPHI, TCQ->SPTN, PTY->MPTO, AQP->SPQU coinciden con FormatoFMP).
"""

SEED_AIRPORTS: dict[str, dict[str, str]] = {
    "AEP": {"icao": "SABE", "name": "Jorge Newbery Airport", "country": "AR", "city": "Buenos Aires"},
    "AMS": {"icao": "EHAM", "name": "Amsterdam Airport Schiphol", "country": "NL", "city": "Amsterdam"},
    "ANF": {"icao": "SCFA", "name": "Cerro Moreno International Airport", "country": "CL", "city": "Antofagasta"},
    "ANS": {"icao": "SPHY", "name": "Andahuaylas", "country": "PE", "city": "Andahuaylas"},
    "ANU": {"icao": "TAPA", "name": "V.C. Bird International Airport", "country": "AG", "city": "Antigua"},
    "AQP": {"icao": "SPQU", "name": "Rodriguez Ballon International Airport", "country": "PE", "city": "Arequipa"},
    "ASU": {"icao": "SGAS", "name": "Silvio Pettirossi International Airport", "country": "PY", "city": "Asuncion"},
    "ATA": {"icao": "SPHZ", "name": "Anta", "country": "PE", "city": "Anta"},
    "ATL": {"icao": "KATL", "name": "Hartsfield-Jackson Atlanta International Airport", "country": "US", "city": "Atlanta"},
    "AUA": {"icao": "TNCA", "name": "Reina Beatrix International Airport", "country": "AW", "city": "Oranjestad"},
    "AYP": {"icao": "SPHO", "name": "Yanamilla Airport", "country": "PE", "city": "Ayacucho"},
    "BCN": {"icao": "LEBL", "name": "Barcelona-El Prat Airport", "country": "ES", "city": "Barcelona"},
    "BOG": {"icao": "SKBO", "name": "El Dorado International Airport", "country": "CO", "city": "Bogota"},
    "BSB": {"icao": "SBBR", "name": "Brasilia International Airport", "country": "BR", "city": "Brasilia"},
    "CAP": {"icao": "MTCH", "name": "Cap Haitien Airport", "country": "HT", "city": "Cap-Haitien"},
    "CDG": {"icao": "LFPG", "name": "Charles de Gaulle Airport", "country": "FR", "city": "Paris"},
    "CHH": {"icao": "SPPY", "name": "Chachapoyas", "country": "PE", "city": "Chachapoyas"},
    "CIX": {"icao": "SPHI", "name": "Cornel Ruiz Airport", "country": "PE", "city": "Chiclayo"},
    "CJA": {"icao": "SPJR", "name": "Cajamarca Airport", "country": "PE", "city": "Cajamarca"},
    "COR": {"icao": "SACO", "name": "Ingeniero Ambrosio L.V. Taravella Airport", "country": "AR", "city": "Cordoba"},
    "CTG": {"icao": "SKCG", "name": "Rafael Nunez International Airport", "country": "CO", "city": "Cartagena"},
    "CUN": {"icao": "MMUN", "name": "Cancun International Airport", "country": "MX", "city": "Cancun"},
    "CUR": {"icao": "TNCC", "name": "Curacao International Airport", "country": "CW", "city": "Curacao"},
    "CUZ": {"icao": "SPZO", "name": "Alejandro Velasco Astete International Airport", "country": "PE", "city": "Cusco"},
    "CWB": {"icao": "SBCT", "name": "Afonso Pena International Airport", "country": "BR", "city": "Curitiba"},
    "DFW": {"icao": "KDFW", "name": "Dallas/Fort Worth International Airport", "country": "US", "city": "Dallas"},
    "EWR": {"icao": "KEWR", "name": "Newark Liberty International Airport", "country": "US", "city": "Newark"},
    "EZE": {"icao": "SAEZ", "name": "Ministro Pistarini Airport", "country": "AR", "city": "Buenos Aires"},
    "FLL": {"icao": "KFLL", "name": "Fort Lauderdale-Hollywood International Airport", "country": "US", "city": "Fort Lauderdale"},
    "FLN": {"icao": "SBFL", "name": "Hercilio Luz International Airport", "country": "BR", "city": "Florianopolis"},
    "FRA": {"icao": "EDDF", "name": "Frankfurt Airport", "country": "DE", "city": "Frankfurt"},
    "GIG": {"icao": "SBGL", "name": "Galeao Antonio Carlos Jobim International Airport", "country": "BR", "city": "Rio de Janeiro"},
    "GRU": {"icao": "SBGR", "name": "Sao Paulo-Guarulhos International Airport", "country": "BR", "city": "Sao Paulo"},
    "GYE": {"icao": "SEGU", "name": "Jose Joaquin de Olmedo Airport", "country": "EC", "city": "Guayaquil"},
    "HAV": {"icao": "MUHA", "name": "Jose Marti International Airport", "country": "CU", "city": "Havana"},
    "HPN": {"icao": "KHPN", "name": "Westchester County Airport", "country": "US", "city": "White Plains"},
    "HUU": {"icao": "SPNC", "name": "Huanuco Airport", "country": "PE", "city": "Huanuco"},
    "IAH": {"icao": "KIAH", "name": "George Bush Intercontinental Airport", "country": "US", "city": "Houston"},
    "ICN": {"icao": "RKSI", "name": "Incheon International Airport", "country": "KR", "city": "Seoul"},
    "IGR": {"icao": "SARI", "name": "Cataratas del Iguazu International Airport", "country": "AR", "city": "Iguazu"},
    "IPC": {"icao": "SCIP", "name": "Mataveri International Airport", "country": "CL", "city": "Easter Island"},
    "IQT": {"icao": "SPQT", "name": "C.F. Secada Vignetta International Airport", "country": "PE", "city": "Iquitos"},
    "JAE": {"icao": "SPJE", "name": "Shumba Airport", "country": "PE", "city": "Jaen"},
    "JAU": {"icao": "SPJJ", "name": "Jauja Airport", "country": "PE", "city": "Jauja"},
    "JFK": {"icao": "KJFK", "name": "John F. Kennedy International Airport", "country": "US", "city": "New York"},
    "JUL": {"icao": "SPJL", "name": "Juliaca Airport", "country": "PE", "city": "Juliaca"},
    # El propio aeródromo de la FMP. Faltaba: mientras el sistema fue
    # mono-aeródromo, Lima era el destino implícito de todo y nunca hubo que
    # buscarlo en el catálogo -- solo se consultaban los ORÍGENES de provincia.
    # Al leer el itinerario de temporada eso dejó de valer: la columna se llama
    # "HORA APROBADA LIM UTC" y hay que reconocer que LIM es SPJC, o el archivo
    # se rechaza entero.
    "LIM": {"icao": "SPJC", "name": "Jorge Chávez International Airport", "country": "PE", "city": "Lima"},
    "KIN": {"icao": "MKJP", "name": "Norman Manley International Airport", "country": "JM", "city": "Kingston"},
    "LAX": {"icao": "KLAX", "name": "Los Angeles International Airport", "country": "US", "city": "Los Angeles"},
    "LPB": {"icao": "SLLP", "name": "El Alto International Airport", "country": "BO", "city": "La Paz"},
    "MAD": {"icao": "LEMD", "name": "Adolfo Suarez Madrid-Barajas Airport", "country": "ES", "city": "Madrid"},
    "MBJ": {"icao": "MKJS", "name": "Sangster International Airport", "country": "JM", "city": "Montego Bay"},
    "MCO": {"icao": "KMCO", "name": "Orlando International Airport", "country": "US", "city": "Orlando"},
    "MDE": {"icao": "SKRG", "name": "Jose Maria Cordova International Airport", "country": "CO", "city": "Medellin"},
    "MDZ": {"icao": "SAME", "name": "El Plumerillo International Airport", "country": "AR", "city": "Mendoza"},
    "MEX": {"icao": "MMMX", "name": "Benito Juarez International Airport", "country": "MX", "city": "Mexico City"},
    "MIA": {"icao": "KMIA", "name": "Miami International Airport", "country": "US", "city": "Miami"},
    "MTY": {"icao": "MMMY", "name": "Gen Mariano Escobedo Airport", "country": "MX", "city": "Monterrey"},
    "MVD": {"icao": "SUMU", "name": "Carrasco International Airport", "country": "UY", "city": "Montevideo"},
    "MZA": {"icao": "SPMF", "name": "Mayor PNP Nancy Flores Airport", "country": "PE", "city": "Mazamari"},
    "NLU": {"icao": "MMSM", "name": "Felipe Angeles International Airport", "country": "MX", "city": "Santa Lucia"},
    "PCL": {"icao": "SPCL", "name": "Capitan Rolden Airport", "country": "PE", "city": "Pucallpa"},
    "PEM": {"icao": "SPTU", "name": "Puerto Maldonado Airport", "country": "PE", "city": "Puerto Maldonado"},
    "PIO": {"icao": "SPSO", "name": "Capitan FAP Renan Elias Olivera Airport", "country": "PE", "city": "Pisco"},
    "PIS": {"icao": "LFBI", "name": "Poitiers-Biard Airport", "country": "FR", "city": "Poitiers"},
    "PIU": {"icao": "SPUR", "name": "Cap. FAP Guillermo Concha Iberico International Airport", "country": "PE", "city": "Piura"},
    "POA": {"icao": "SBPA", "name": "Salgado Filho International Airport", "country": "BR", "city": "Porto Alegre"},
    "PTY": {"icao": "MPTO", "name": "Tocumen International Airport", "country": "PA", "city": "Panama City"},
    "PUJ": {"icao": "MDPC", "name": "Punta Cana International Airport", "country": "DO", "city": "Punta Cana"},
    "QSC": {"icao": "SDSC", "name": "Sao Carlos", "country": "BR", "city": "Sao Carlos"},
    "ROS": {"icao": "SAAR", "name": "Fisherton Airport", "country": "AR", "city": "Rosario"},
    "SAL": {"icao": "MSLP", "name": "El Salvador International Airport", "country": "SV", "city": "San Salvador"},
    "SCL": {"icao": "SCEL", "name": "Arturo Merino Benitez Airport", "country": "CL", "city": "Santiago"},
    "SDQ": {"icao": "MDSD", "name": "Las Americas International Airport", "country": "DO", "city": "Santo Domingo"},
    "SJO": {"icao": "MROC", "name": "Juan Santamaria International Airport", "country": "CR", "city": "San Jose"},
    "SLA": {"icao": "SASA", "name": "Martin Miguel de Guemes International Airport", "country": "AR", "city": "Salta"},
    "SLC": {"icao": "KSLC", "name": "Salt Lake City International Airport", "country": "US", "city": "Salt Lake City"},
    "SXM": {"icao": "TNCM", "name": "Princess Juliana International Airport", "country": "SX", "city": "Sint Maarten"},
    "TBP": {"icao": "SPME", "name": "Capitan FAP Pedro Canga Rodriguez Airport", "country": "PE", "city": "Tumbes"},
    "TCQ": {"icao": "SPTN", "name": "Tacna Airport", "country": "PE", "city": "Tacna"},
    "TGI": {"icao": "SPGM", "name": "Tingo Maria", "country": "PE", "city": "Tingo Maria"},
    "TPP": {"icao": "SPST", "name": "Cad. FAP Guillermo del Castillo Paredes Airport", "country": "PE", "city": "Tarapoto"},
    "TRU": {"icao": "SPRU", "name": "Trujillo Airport", "country": "PE", "city": "Trujillo"},
    "TUC": {"icao": "SANT", "name": "Teniente Benjamin Matienzo Airport", "country": "AR", "city": "Tucuman"},
    "TYL": {"icao": "SPYL", "name": "Captain FAP Victor Montes Arias Airport", "country": "PE", "city": "Talara"},
    "UIO": {"icao": "SEQM", "name": "Mariscal Sucre International Airport", "country": "EC", "city": "Quito"},
    "VCP": {"icao": "SBKP", "name": "Viracopos Airport", "country": "BR", "city": "Campinas"},
    "VVI": {"icao": "SLVR", "name": "Viru Viru International Airport", "country": "BO", "city": "Santa Cruz"},
    "VVN": {"icao": "SPWT", "name": "Malvinas", "country": "PE", "city": "Pangoa"},
    "YUL": {"icao": "CYUL", "name": "Montreal-Pierre Elliott Trudeau International Airport", "country": "CA", "city": "Montreal"},
    "YYZ": {"icao": "CYYZ", "name": "Pearson International Airport", "country": "CA", "city": "Toronto"},
    # Sin confirmar (pendiente de validar con DGAC): posiblemente vuelos de
    # la Policía Nacional, no un aeródromo real. Mientras se confirma, se
    # deja pasar tal cual (sin forzar una conversión IATA->OACI que podría
    # ser incorrecta) -- corregir vía PUT /airports/PNM en cuanto se sepa.
    "PNM": {"icao": "PNM", "name": "(sin confirmar -- posible código de Policía Nacional)", "country": "", "city": ""},
    # Sin confirmar todavía -- parece un error de tipeo en el archivo origen
    # (ya tiene forma de código OACI, no IATA). Se deja pasar tal cual.
    "SPLP": {"icao": "SPLP", "name": "(posible error de tipeo: ya parece código OACI)", "country": "", "city": ""},
}
