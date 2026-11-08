"""Labelled citation set (B4; B6 reuses it for passages).

Each item: (claim text, claim subject, quote, label) where the label says
what the QUOTE, read on its own, does for the claim - decided by a person:
  supports     states exactly the claim's fact
  contradicts  states the opposite / another value
  partial      only part of it, or only with a condition, date, hedge,
               context or extra content that changes what is stated
  unrelated    about something else

The rule under test (brain/respond.quote_supports) may refuse genuine
support (counted as refusals) but must NEVER accept anything not labelled
"supports" (zero false support).
"""
ITEMS = [
    # --- supports -------------------------------------------------------------------
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "Mira maintains the Jardín Nube.", "supports"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "Mira is the caretaker of the Jardín Nube.",
     "supports"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "The Jardín Nube is maintained by Mira.",
     "supports"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "  Mira maintains the Jardín Nube  ", "supports"),
    ("El encargado del Jardín Nube es Mira.", "Jardín Nube", "Mira es la encargada del Jardín Nube.",
     "supports"),
    ("The Torre Nube has room ID 500200.", "Torre Nube", "La Torre Nube tiene el ID 500200.", "supports"),
    ("The Faro Nuevo has no caretaker.", "Faro Nuevo", "La Faro Nuevo no tiene encargado.", "supports"),
    ("The north gate of the Plaza Nube leads to the Jardín Nube.", "Plaza Nube",
     "The north gate of the Plaza Nube leads to the Jardín Nube.", "supports"),
    ("La misión del Mercado Nube da 40 monedas.", "Mercado Nube",
     "The quest in the Mercado Nube gives 40 coins.", "supports"),
    ("Zoe runs the Plaza Nube.", "Plaza Nube", "Zoe manages the Plaza Nube.", "supports"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "Mira takes care of the Jardín Nube; "
     "she is very good.", "supports"),                                 # extra clause: refusal ok
    ("The Torre Nube has room ID 500200.", "Torre Nube",
     "Torre Nube | room ID | 500200", "supports"),                    # table row: refusal ok
    # --- contradicts ------------------------------------------------------------------
    ("Bruno maintains the Muelle Nube.", "Muelle Nube", "Carla maintains the Muelle Nube.",
     "contradicts"),
    ("The Sala Nube has room ID 500300.", "Sala Nube", "The Sala Nube does not have room ID 500300.",
     "contradicts"),
    ("The Sala Nube has room ID 500300.", "Sala Nube", "The Sala Nube has room ID 500301.",
     "contradicts"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "Mira does not maintain the Jardín Nube.",
     "contradicts"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "The Jardín Nube has no caretaker.",
     "contradicts"),
    ("The Faro Nuevo has no caretaker.", "Faro Nuevo", "Mira maintains the Faro Nuevo.",
     "contradicts"),
    ("The north gate of the Plaza Nube leads to the Jardín Nube.", "Plaza Nube",
     "The south gate of the Plaza Nube leads to the Jardín Nube.", "contradicts"),
    ("La misión del Mercado Nube da 40 monedas.", "Mercado Nube",
     "La misión del Mercado Nube da 50 monedas.", "contradicts"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "Nobody maintains the Jardín Nube.",
     "contradicts"),
    # --- partial: conditions, dates, hedges, reported speech, context, lists -------------
    ("Lena maintains the Patio Nube.", "Patio Nube", "On weekends, Lena maintains the Patio Nube.",
     "partial"),
    ("Lena maintains the Patio Nube.", "Patio Nube", "If it rains, Lena maintains the Patio Nube.",
     "partial"),
    ("Lena maintains the Patio Nube.", "Patio Nube", "Si llueve, Lena cuida el Patio Nube.", "partial"),
    ("Ivo runs the Taller Nube.", "Taller Nube", "Ivo used to run the Taller Nube.", "partial"),
    ("Ivo runs the Taller Nube.", "Taller Nube", "Until 2024 Ivo runs the Taller Nube.", "partial"),
    ("Ivo runs the Taller Nube.", "Taller Nube", "Since March, Ivo runs the Taller Nube.", "partial"),
    ("Ivo runs the Taller Nube.", "Taller Nube", "Ivo will run the Taller Nube.", "partial"),
    ("Ivo runs the Taller Nube.", "Taller Nube", "Antes Ivo dirige el Taller Nube.", "partial"),
    ("Tomás maintains the Huerto Nube.", "Huerto Nube", "Maybe Tomás maintains the Huerto Nube.",
     "partial"),
    ("Tomás maintains the Huerto Nube.", "Huerto Nube", "I think Tomás maintains the Huerto Nube.",
     "partial"),
    ("Tomás maintains the Huerto Nube.", "Huerto Nube", "Tomás probably maintains the Huerto Nube.",
     "partial"),
    ("Tomás maintains the Huerto Nube.", "Huerto Nube", "Some say Tomás maintains the Huerto Nube.",
     "partial"),
    ("Tomás maintains the Huerto Nube.", "Huerto Nube",
     "Dicen que Tomás cuida el Huerto Nube.", "partial"),
    ("Tomás maintains the Huerto Nube.", "Huerto Nube",
     "Reportedly Tomás maintains the Huerto Nube.", "partial"),
    ("Max maintains the Mirador Nube.", "Mirador Nube",
     "In the old version, Max maintains the Mirador Nube.", "partial"),
    ("Max maintains the Mirador Nube.", "Mirador Nube",
     "Formerly Max maintains the Mirador Nube.", "partial"),
    ("Eva runs the Club Nube.", "Club Nube", "Eva runs the Club Nube and the Bar Nube.", "partial"),
    ("Eva runs the Club Nube.", "Club Nube", "Eva and Leo run the Club Nube.", "partial"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube",
     "Mira maintains the Jardín Nube. Leo maintains the Plaza Nube.", "partial"),
    ("The south exit leads to the Faro Nuevo.", "Torre Nube",
     "The south exit leads to the Faro Nuevo.", "partial"),           # place only from the subject
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "Mira maintains it.", "partial"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "Mira maintains the garden.", "partial"),
    ("The Torre Nube has room ID 500200.", "Torre Nube", "Room ID 500200.", "partial"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "", "partial"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "Does Mira maintain the Jardín Nube?",
     "partial"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "Someone maintains the Jardín Nube.",
     "partial"),
    # --- unrelated --------------------------------------------------------------------
    ("Sol maintains the Cueva Nube.", "Cueva Nube", "Sol maintains the Cueva Nube Norte.", "unrelated"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "Mira maintains the Jardín Nube 2.",
     "unrelated"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "Mira runs the Jardín Nube.", "unrelated"),
    ("The Torre Nube has room ID 500200.", "Torre Nube", "The Plaza Nube has room ID 500200.",
     "unrelated"),
    ("Mira maintains the Jardín Nube.", "Jardín Nube", "The Jardín Nube is lovely in spring.",
     "unrelated"),
    ("The north gate of the Plaza Nube leads to the Jardín Nube.", "Plaza Nube",
     "The north gate of the Plaza Nube is red.", "unrelated"),
]
