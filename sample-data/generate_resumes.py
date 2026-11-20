"""Generate ten synthetic DOCX resumes with fictional identities."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape

OUT=Path(__file__).parent/"resumes"
people=[
 ("Avery Chen","avery.chen@example.test","Backend Engineer","Python FastAPI PostgreSQL Docker REST API", "Built APIs for a scheduling platform. Improved request latency and wrote integration tests.","2021","2025","Bachelor of Computer Science"),
 ("Jordan Patel","jordan.patel@example.test","Full Stack Developer","JavaScript TypeScript React Node SQL Git", "Delivered an internal inventory dashboard and REST endpoints.","2022","2025","Bachelor of Information Technology"),
 ("Morgan Okafor","morgan.okafor@example.test","Machine Learning Engineer","Python scikit-learn machine learning NLP AWS", "Created text classification experiments and a model evaluation pipeline.","2019","2025","Master of Data Science"),
 ("Riley Kim","riley.kim@example.test","Platform Engineer","Python Kubernetes Docker AWS Redis Linux", "Automated deployment workflows and monitored services across clusters.","2018","2024","Bachelor of Software Engineering"),
 ("Samira Das","samira.das@example.test","Python Developer","Python Django PostgreSQL Redis REST API", "Implemented reporting tools and customer case management services.","2020","2025","Bachelor of Computer Applications"),
 ("Casey Morgan","casey.morgan@example.test","Frontend Engineer","React TypeScript JavaScript HTML CSS", "Built accessible recruitment and analytics interfaces with reusable components.","2022","2025","Bachelor of Design"),
 ("Taylor Brooks","taylor.brooks@example.test","Data Analyst","SQL Python scikit-learn PostgreSQL", "Prepared operational datasets and measured support response trends.","2021","2024","Bachelor of Statistics"),
 ("Noor Ahmed","noor.ahmed@example.test","Cloud Developer","Java Python Azure Docker Git", "Maintained a document intake service and automated cloud deployment checks.","2017","2025","Master of Computer Science"),
 ("Jamie Wilson","jamie.wilson@example.test","Junior Software Developer","JavaScript Node React MongoDB Git", "Prototyped a community event portal and built simple API integrations.","2023","2025","Bachelor of Information Systems"),
 ("Devon Singh","devon.singh@example.test","Software Engineer","Python FastAPI SQL RabbitMQ Docker scikit-learn", "Developed async job processing for a logistics prototype and explored ranking models.","2019","2025","Master of Software Engineering"),
]

def make_docx(path, person):
 name,email,title,skills,project,start,end,education=person
 sections=[name,email,title,"", "SUMMARY",f"{title} with practical experience building software and collaborating across product teams.","", "TECHNICAL SKILLS",skills,"", "WORK EXPERIENCE",f"{title} | Example Works | {start} - {end}",project,"", "EDUCATION",education,"", "PROJECTS",project,"", "CERTIFICATIONS","No certifications listed"]
 paragraphs="".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(line)}</w:t></w:r></w:p>" for line in sections)
 document=f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{paragraphs}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>'''
 with ZipFile(path,"w",ZIP_DEFLATED) as z:
  z.writestr("[Content_Types].xml",'''<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>''')
  z.writestr("_rels/.rels",'''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>''')
  z.writestr("word/document.xml",document)

OUT.mkdir(exist_ok=True)
for i,p in enumerate(people,1): make_docx(OUT/f"candidate-{i:02d}.docx",p)
print(f"Created {len(people)} synthetic resumes in {OUT}")
