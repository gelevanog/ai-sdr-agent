"""Wording pools for the synthetic websites. The planted facts come from data/companies.yaml; these pools only fill
the pages around them so that every site reads like a small company website and no two look alike."""

from __future__ import annotations

FLEET_SEGMENTS = frozenset(
    {
        "last_mile_delivery",
        "freight_trucking",
        "field_services",
        "utilities_contracting",
        "waste_management",
        "construction",
        "food_beverage_distribution",
        "wholesale_distribution",
        "equipment_rental",
        "passenger_transport",
        "mobile_healthcare",
    }
)

COUNTRY_NAMES = {
    "US": "United States",
    "CA": "Canada",
    "GB": "United Kingdom",
    "IE": "Ireland",
    "DE": "Germany",
    "NL": "Netherlands",
    "BE": "Belgium",
    "FR": "France",
    "AT": "Austria",
    "NO": "Norway",
    "SE": "Sweden",
    "DK": "Denmark",
    "FI": "Finland",
    "AU": "Australia",
    "PE": "Peru",
    "JP": "Japan",
}

SERVICES: dict[str, list[str]] = {
    "last_mile_delivery": [
        "Same-day delivery",
        "Scheduled multi-drop routes",
        "Returns collection",
        "Proof of delivery with photos",
        "Temperature-controlled courier runs",
    ],
    "freight_trucking": [
        "Full truckload",
        "Less-than-truckload",
        "Dedicated contract carriage",
        "Cross-docking",
        "Seasonal surge capacity",
    ],
    "field_services": [
        "Planned maintenance visits",
        "Emergency call-outs",
        "Installations",
        "Service contracts for businesses",
        "Annual inspections",
    ],
    "utilities_contracting": [
        "Overhead line construction",
        "Underground cable work",
        "Gas main replacement",
        "Storm response crews",
        "Substation maintenance",
    ],
    "waste_management": [
        "Residential kerbside collection",
        "Commercial bins",
        "Recycling and sorting",
        "Construction waste removal",
        "Bulky item pickup",
    ],
    "construction": [
        "General contracting",
        "Design-build",
        "Site preparation",
        "Tenant improvements",
        "Equipment-intensive civil works",
    ],
    "food_beverage_distribution": [
        "Chilled and frozen deliveries",
        "Next-morning restaurant drops",
        "Warehouse picking",
        "Route accounts for independent shops",
        "Seasonal promotions support",
    ],
    "wholesale_distribution": [
        "Next-day business delivery",
        "Bulk office supply orders",
        "Facilities and janitorial products",
        "Furniture installation",
        "Online ordering portal",
    ],
    "equipment_rental": [
        "Excavators and loaders",
        "Aerial work platforms",
        "Generators and lighting towers",
        "Delivery and pickup",
        "On-site maintenance",
    ],
    "passenger_transport": [
        "Scheduled shuttle routes",
        "Charter trips",
        "Accessible transport",
        "Event transport",
        "Contract routes for employers",
    ],
    "mobile_healthcare": [
        "Mobile MRI and CT",
        "On-site screening days",
        "Hospital overflow capacity",
        "Occupational health visits",
        "Rural clinic rotations",
    ],
    "games": ["Starfleet Tactics (PC and console)", "Live events and seasons", "Community tournaments", "Mod tools"],
    "restaurants": [
        "Breakfast all day",
        "Family meal deals",
        "Online ordering through delivery apps",
        "Catering trays",
    ],
    "software": [
        "Route optimisation API",
        "Dispatch planning dashboard",
        "ETA predictions",
        "Integrations with TMS and ERP tools",
    ],
    "media": ["The morning Dispatch newsletter", "Industry briefings", "Sponsored events", "Podcast"],
    "finance": ["Private equity funds", "Infrastructure investments", "Co-investment programmes", "Investor reporting"],
    "manufacturing": ["Small-batch production", "Wholesale accounts", "Seasonal releases", "Tours and tastings"],
    "retail": ["Bouquets and arrangements", "Wedding flowers", "Same-day local delivery", "Subscriptions"],
    "telematics_vendor": [
        "GPS vehicle tracking",
        "Driver behaviour scoring",
        "ELD and tachograph compliance",
        "Fuel reports",
        "Open API",
    ],
    "healthcare_clinics": ["General dentistry", "Orthodontics", "Hygiene appointments", "Emergency dental care"],
    "hardware": ["Parcel lockers", "Locker-as-a-service for retailers", "Carrier integrations", "Returns drop-off"],
    "leisure_rental": ["City bike rental", "Guided bike tours", "E-bike rental", "Group bookings"],
    "travel": ["Package holidays", "Rail journeys", "Group travel", "Business travel management"],
}

TAGLINES: dict[str, list[str]] = {
    "fleet": [
        "Reliable crews, on time, every time.",
        "Local people, serious equipment.",
        "Built on routes our customers can count on.",
        "Keeping {served} moving.",
        "Service you can set your watch by.",
    ],
    "other": [
        "Made with care since {founded}.",
        "Doing one thing well.",
        "Small team, big ambitions.",
        "Trusted by customers across {served}.",
    ],
}

VALUES = [
    "Safety comes first on every job, and we review every incident with the crew involved.",
    "We hire locally and train our own people.",
    "Customers get a named contact, not a call centre.",
    "We publish our service standards and report on them every quarter.",
    "We invest in equipment so our crews can do the job right the first time.",
    "Our people are the reason customers stay with us for years.",
    "We answer the phone, even at weekends.",
]

FILLER_NEWS = [
    "{name} sponsored the {city} youth football league for the third year running.",
    "Our crews raised money for the local food bank during the winter appeal.",
    "We were named one of the best places to work in {city} by the regional business journal.",
    "{name} completed its annual safety stand-down with every team.",
    "We refreshed our website and customer portal.",
    "Our apprentices graduated from the company training programme.",
    "{name} welcomed customers to an open day at our {city} yard.",
]

FILLER_PRESS = [
    "{name} renews its partnership with the {city} chamber of commerce.",
    "{name} publishes its annual sustainability report.",
    "{name} achieves ISO 9001 recertification.",
]

NOISE_JOBS: dict[str, list[str]] = {
    "last_mile_delivery": ["Delivery Driver", "Warehouse Associate", "Customer Service Agent"],
    "freight_trucking": ["CDL Truck Driver", "Diesel Mechanic", "Billing Specialist"],
    "field_services": ["Service Technician", "Apprentice", "Customer Care Representative", "Estimator"],
    "utilities_contracting": ["Lineworker", "Groundworker", "Project Accountant"],
    "waste_management": ["Collection Driver", "Loader", "Recycling Plant Operator"],
    "construction": ["Site Superintendent", "Project Engineer", "Carpenter"],
    "food_beverage_distribution": ["Delivery Driver", "Warehouse Picker", "Sales Representative"],
    "wholesale_distribution": ["Delivery Driver", "Inside Sales Representative", "Warehouse Associate"],
    "equipment_rental": ["Rental Coordinator", "Heavy Equipment Mechanic", "Delivery Driver"],
    "passenger_transport": ["Shuttle Driver", "Bus Mechanic", "Customer Service Agent"],
    "mobile_healthcare": ["MRI Technologist", "Scheduling Coordinator", "Driver and Site Assistant"],
    "games": ["Senior Gameplay Programmer", "3D Artist", "Community Manager"],
    "restaurants": ["Line Cook", "Shift Manager", "Server"],
    "software": ["Senior Backend Engineer", "Account Executive", "Product Designer"],
    "media": ["Reporter", "Audience Editor", "Sales Manager"],
    "finance": ["Investment Associate", "Fund Accountant", "Investor Relations Manager"],
    "manufacturing": ["Production Assistant", "Sales Manager"],
    "retail": ["Florist", "Delivery Driver (part-time)"],
    "telematics_vendor": ["Account Executive", "Embedded Engineer", "Customer Success Manager"],
    "healthcare_clinics": ["Dental Nurse", "Receptionist", "Dentist"],
    "hardware": ["Field Technician", "Partnerships Manager", "Software Engineer"],
    "leisure_rental": ["Tour Guide", "Bike Mechanic"],
    "travel": ["Travel Consultant", "Marketing Executive"],
}

JOB_BLURBS = [
    "You will join a team that cares about doing things properly.",
    "Full training, a company pension and paid holidays.",
    "Competitive pay, overtime available.",
    "This is a full-time, permanent position.",
    "We are looking for someone organised who enjoys working with people.",
]

FLEET_JOB_BLURBS = [
    "You will own vehicle maintenance schedules, driver safety reviews and fleet cost reporting.",
    "The role covers vehicle allocation, compliance checks and working with drivers on safe driving.",
    "You will keep our vehicles on the road, track incidents and report on fuel and utilisation.",
]

PRICING: dict[str, list[str]] = {
    "fleet": [
        "Standard call-out or delivery fees are quoted per job.",
        "Business accounts get monthly invoicing and volume pricing.",
        "Contact us for a written quote within one working day.",
    ],
    "software": ["Starter: $499 per month", "Growth: $1,499 per month", "Enterprise: contact sales"],
    "other": ["Prices are listed in our shop and online.", "Ask us about group and business rates."],
}

FIRST_NAMES: dict[str, list[str]] = {
    "en": [
        "Olivia",
        "James",
        "Amelia",
        "Noah",
        "Grace",
        "Ethan",
        "Chloe",
        "Liam",
        "Maya",
        "Owen",
        "Ruth",
        "Daniel",
        "Priya",
        "Samuel",
        "Leah",
        "Tom",
        "Nadia",
        "Hassan",
        "Megan",
        "Victor",
        "Imani",
        "Connor",
        "Rosa",
        "Felix",
    ],
    "de": [
        "Lukas",
        "Anna",
        "Jonas",
        "Lea",
        "Felix",
        "Sophie",
        "Moritz",
        "Clara",
        "Tobias",
        "Hannah",
        "Stefan",
        "Miriam",
    ],
    "nl": ["Daan", "Fleur", "Sem", "Lotte", "Bram", "Eva", "Thijs", "Noor", "Ruben", "Iris"],
    "fr": ["Camille", "Louis", "Manon", "Hugo", "Chloé", "Lucas", "Inès", "Théo", "Léa", "Mathieu"],
    "nordic": ["Erik", "Ingrid", "Lars", "Sofie", "Magnus", "Astrid", "Henrik", "Maja", "Nils", "Freja"],
    "ja": ["Haruto", "Yui", "Sota", "Hina", "Ren", "Aoi", "Kenji", "Sakura"],
    "es": ["Mateo", "Valeria", "Diego", "Camila", "Andrés", "Lucía", "Javier", "Sofía"],
}

LAST_NAMES: dict[str, list[str]] = {
    "en": [
        "Carter",
        "Hughes",
        "Patel",
        "Nguyen",
        "Morrison",
        "Bennett",
        "Okoro",
        "Fitzgerald",
        "Larsen",
        "Chen",
        "Delgado",
        "Kowalski",
        "Ashworth",
        "Murphy",
        "Campbell",
        "Ellison",
        "Rahman",
        "Sullivan",
        "Whitaker",
        "Lindqvist",
        "Abara",
        "Moreau",
        "Yates",
        "Brennan",
    ],
    "de": ["Becker", "Schneider", "Hoffmann", "Wagner", "Krüger", "Neumann", "Brandt", "Lehmann", "Zimmermann", "Roth"],
    "nl": ["Bakker", "Visser", "Jansen", "Smit", "Meijer", "de Boer", "Mulder", "Bos", "van Dijk", "Hendriks"],
    "fr": ["Lefèvre", "Girard", "Bonnet", "Dupont", "Lambert", "Fontaine", "Rousseau", "Mercier"],
    "nordic": ["Hansen", "Johansen", "Berg", "Lund", "Nilsen", "Dahl", "Holm", "Strand"],
    "ja": ["Tanaka", "Suzuki", "Takahashi", "Watanabe", "Ito", "Yamamoto"],
    "es": ["García", "Rojas", "Flores", "Mendoza", "Vargas", "Castillo"],
}

LOCALE_BY_COUNTRY = {
    "US": "en", "CA": "en", "GB": "en", "IE": "en", "AU": "en",
    "DE": "de", "AT": "de", "NL": "nl", "BE": "nl", "FR": "fr",
    "NO": "nordic", "SE": "nordic", "DK": "nordic", "FI": "nordic",
    "JP": "ja", "PE": "es",
}  # fmt: skip

STREETS = [
    "Industrial Way",
    "Depot Road",
    "Commerce Drive",
    "Harbour Lane",
    "Station Street",
    "Mill Road",
    "Enterprise Park",
    "Riverside Avenue",
]

ACCENTS = ["#0f766e", "#1d4ed8", "#b45309", "#7c3aed", "#be123c", "#15803d", "#0369a1", "#a16207", "#4d7c0f", "#9f1239"]
