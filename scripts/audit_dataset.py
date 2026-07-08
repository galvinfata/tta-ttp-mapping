#!/usr/bin/env python3
"""
verify_dataset.py - Langkah A1b: cocokkan folder data/tram_ii dengan
daftar resmi 151 file .mjson dari repo CTID TRAM (main branch).

Cara pakai: salin ke root proyek (sejajar audit_dataset.py), jalankan:
    python verify_dataset.py
Lalu salin SELURUH output ke Claude. Read-only, tidak mengubah apa pun.
"""
import re
from pathlib import Path

DATA_DIR = Path("data/tram_ii")

OFFICIAL = set([
"3cxdesktopappbackdooredinasuspectedlazaruscampaign",
"aa20258achineseministryofstatesecurityaffiliatedcyberthreatactoractivity",
"aa20336aadvancedpersistentthreatactorstargetingusthinktanks",
"aa21076atrickbotmalware",
"aa21200atacticstechniquesandproceduresofindictedapt40actorsassociatedwithchinasmsshainanstatesecuritydepartment",
"aa21200bchinesestatesponsoredcyberoperationsobservedttps",
"aa22320airaniangovernmentsponsoredaptactorscompromisefederalnetworkdeploycryptominercredentialharvester",
"abusingcloudservicestoflyundertheradar",
"akiraransomwareisbringin1988back",
"alookbackundertheta410umbrellaitscyberespionagettpsandactivity",
"analysisonrecentwiperattacksexamplesandhowwipermalwareworks",
"analyzingsolorigatethecompromiseddllfilethatstartedasophisticatedcyberattackandhowmicrosoftdefenderhelpsprotectcustomers",
"attackersusedomainfrontingtechniquetotargetmyanmarwithcobaltstrike",
"babadedacryptertargetingcryptonftanddeficommunities",
"bankingtrojantechniqueshowfinanciallymotivatedmalwarebecameinfrastructure",
"bazarloadermocksresearchersindecember2020malspamcampaign",
"beewareoftrigonaanemergingransomwarestrain",
"bluenoroffintroducesnewmethodsbypassingmotw",
"breakingpedersenhashesinpractice",
"bumblebeeroastsitswaytodomainadmin",
"bypassingintelcetwithcounterfeitobjectsoffsec",
"canyouseeitnowanemerginglockbitcampaign",
"carbonblackstruebotdetection",
"catbransomwarefilelockersharpensitsclawstostealdatawithmsdtcservicedllhijacking",
"chinesethreatactorusedmodifiedcobaltstrikevarianttoattacktaiwanesecriticalinfrastructure",
"cisaredteamshareskeyfindingstoimprovemonitoringandhardeningofnetworks",
"cobaltstrikeadefendersguidepart2",
"contiransomware",
"contiteamonesplintergroupresurfacesasroyalransomwarewithcallbackphishingattacks",
"crowdstrikeuncoversi2pminermacosminewarevariant",
"darkwebprofilemuddywateraptgroup",
"deadoraliveanemotetstory",
"defendingusersnasdevicesfromevolvingthreats",
"dejavualloveragaintaxscammersatlarge",
"detectingcredentialstealingattacksthroughactiveinnetworkdefense",
"dissectingoneofapt29sfilelesswmiandpowershellbackdoorsposhspy",
"donotcrosstheredlinestealerdetectionsandanalysis",
"earlybirdcatchesthewormholeobservationsfromthestellarparticlecampaign",
"earthpretascyberespionagecampaignhitsover200",
"earthzhulongfamiliarpatternstargetsoutheastasianfirms",
"emotetstrikesagainlnkfileleadstodomainwideransomwarethedfirreport",
"enigmastealertargetscryptocurrencyindustrywithfakejobs",
"esentirethreatintelligencemalwareanalysisbatloader",
"evasivenoescaperansomwareusesreflectivedllinjection",
"evilextractorallinonestealer",
"excontiandfin7actorscollaboratewithnewdominobackdoor",
"fantasyanewagriuswiperdeployedthroughasupplychainattack",
"fatcats",
"fedexphishingcampaignabusingtrustedformandpaay",
"finspyunseenfindings",
"forkintheicetheneweraoficedid",
"germanuserstargetedwithgootkitbankerorrevilransomware",
"gobruteforcergolangbasedbotnetactivelyharvestswebservers",
"gottacatchemallunderstandingthenetsupportratcampaignshidingbehindpokemonlures",
"guloaderdemystifiedunravelingitsvectoredexceptionhandlerapproach",
"guloadervbscriptvariantreturnswithpowershellupdates",
"hafniuminspiredcyberattacksneutralizedbyai",
"hancitorinfectionchainanalysisanexaminationofitsunpackingroutineandexecutiontechniques",
"higaisaorwinntiapt41backdoorsoldandnew",
"horabotcampaigntargetedbusinessesformorethantwoyearsbeforefinallybeingdiscovered",
"howtodetectcobaltstrike",
"hungryfordatamodpipebackdoorhitspossoftwareusedinhospitalitysector",
"increasingthestingofhiveransomware",
"insidethemindofacyberattackerfrommalwarecreationtodataexfiltrationpart1",
"inthefootstepsofthefancybearpowerpointmouseovereventabusedtodelivergraphiteimplants",
"investigationwithatwistanaccidentalaptattackandaverteddatadestruction",
"iraniangovernmentsponsoredaptactorscompromisefederalnetworkdeploycryptominercredentialharvester",
"ironnetinjectorturlasnewmalwareloadingtool",
"irontigerssysupdatereappearsaddslinuxtargeting",
"itg10likelytargetingsouthkoreanentitiesofinteresttothedemocraticpeoplesrepublicofkoreadprk",
"justbecauseitsolddoesntmeanyouthrowitawayincludingmalware",
"kimsukystrikesagainnewsocialengineeringcampaignaimstostealcredentialsandgatherstrategicintelligence",
"lapsusrecenttechniquestacticsandprocedures",
"latinamericangovernmentstargetedbyransomware",
"linuxmalwarestrengthenslinksbetweenlazarusandthe3cxsupplychainattack",
"lockbit20howthisraasoperatesandhowtoprotectagainstit",
"lockbitransomware20resurfaces",
"maliciousisofileleadstodomainwideransomwarethedfirreport",
"maliciousoauthapplicationsusedtocompromiseemailserversandspreadspammicrosoftsecurityblog",
"malwareanalysislummac2stealer",
"malwaredisguisedasdocumentfromukrainesenergoatomdelivershavocdemonbackdoor",
"malwarereverseengineeringforbeginnerspart2",
"malwarespotlightcamarodragonstinynotebackdoor",
"mcafeedefendersblognetwalker",
"mercuryanddev1084destructiveattackonhybridenvironment",
"microsoftresearchuncoversnewzerobotcapabilitiesmicrosoftsecurityblog",
"moonbouncethedarksideofuefifirmware",
"nationstatethreatactormintsandstormrefinestradecrafttoattackhighvaluetargets",
"nefilimransomware",
"newhorabotcampaigntargetstheamericas",
"newicedidvariantsshiftfrombankfraudtomalwaredelivery",
"newrapperbotcampaignweknowwhatyoubrutingforthistime",
"notjustaninfostealergopurambackdoordeployedthrough3cxsupplychainattack",
"notpetyatechnicalanalysisatriplethreatfileencryptionmftencryptioncredentialtheft",
"opensourcegh0stratstillhauntinginboxes15yearsafterrelease",
"operationcmdstealerfinanciallymotivatedcampaignleveragescmdbasedscriptsandlolbasforonlinebankingtheftinportugalperuandmexico",
"operationharvestadeepdiveintoalongtermcampaign",
"operationspalaxtargetedmalwareattacksincolombia",
"operationtaintedlovechineseaptstargettelcosinnewattacks",
"packitsecretlyearthpretasupdatedstealthystrategies",
"phishingcampaigntargetschinesenuclearenergyindustry",
"prilexbrazilianposmalwareevolution",
"qakbotreturnstoisodeliveryfornow",
"ransomcartelransomwareapossibleconnectionwithrevil",
"recenttzwcampaignsrevealedaspartofglobeimpostermalwarefamily",
"revisitingthensisbasedcrypter",
"rorschachanewsophisticatedandfastransomwarecheckpointresearch",
"seroxenratforsale",
"sharppandaaptcampaignexpandsitsarsenaltargetingg20nations",
"smokingoutadarksideaffiliatessupplychainsoftwarecompromise",
"socteamessentialshowtoinvestigateandtrackthe8220gangcloudthreat",
"spikeinlokibotactivityduringfinalweekof2022",
"stolencertificatesintwowavesofransomwareandwiperattacks",
"stopransomwarehiveransomware",
"stopransomwareroyalransomware",
"supplychainriskfromgigabyteappcenterbackdooreclypsiumsupplychainsecurityforthemodernenterprise",
"sys01stealerwillstealyourfacebookinfo",
"tailoringsandboxtechniquestohiddenthreats",
"takeanetwalkonthewildside",
"taxfirmstargetedbyprecisionmalwareattacks",
"technicalanalysisblackbastamalwareoverview",
"thelockbitransomwarekindacomesformacos",
"therisingtrendofonenotedocumentsformalwaredelivery",
"thesearenttheappsyourelookingforfakeinstallerstargetingsoutheastandeastasia",
"threatactorsstrivetocausetaxdayheadaches",
"threatactorsusemsbuildtodeliverratsfilelessly",
"threatadvisory3cxsoftphonesupplychaincompromise",
"threatassessmentblackbastaransomware",
"threatassessmentblackcatransomware",
"tomiriscalledtheywanttheirturlamalwareback",
"toologdidntreadunknownactorusingclfslogfilesforstealth",
"trackingtracesofmalwaredisguisedashancomofficedocumentfileandbeingdistributedredeyes",
"transparenttribeapt36pakistanalignedthreatactorexpandsinterestinindianeducationsector",
"unc215spotlightonachineseespionagecampaigninisrael",
"uncommoninfectionmethodspart2",
"understandingdnsattacksidentifyingandpatchingvulnerabilitiessnyk",
"unwrappingursnifsgiftsthedfirreport",
"update23cxusersunderdllsideloadingattackwhatyouneedtoknow",
"updatednewevidenceemergestosuggestwatchdogwasbehindcryptocampaign",
"vicesocietyleveragesprintnightmareinransomwareattacks",
"vicesocietyprofilingapersistentthreattotheeducationsector",
"vipersoftxupdatesencryptionstealsdata",
"vulnerabilityinessentialaddonsforelementorleadstomassinfection",
"warningnewattackcampaignutilizedanew0dayrcevulnerabilityonmicrosoftexchangeserver",
"whenbytecodebiteswhochecksthecontentsofcompiledpythonfiles",
"whosswimminginsouthkoreanwatersmeetscarcruftsdolphin",
"wtbadwindtrojancircumventsantivirussoftwaretoinfectyourpc",
"wtbremotemacexploitationviacustomurlschemes",
"xollamthelatestfaceoftargetcompany",
"zerodayvulnerabilityinmoveittransferexploitedfordatatheft",
"zipjaralittlebitunexpectedattackchain"
])

def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())

def main() -> None:
    if not DATA_DIR.exists():
        print(f"[!] Folder {DATA_DIR} tidak ditemukan. Jalankan dari root proyek.")
        return

    mjson = sorted(f for f in DATA_DIR.rglob("*.mjson") if f.is_file())
    others = sorted(f for f in DATA_DIR.rglob("*") if f.is_file() and f.suffix != ".mjson")

    local = {norm(f.stem): f.name for f in mjson}

    print("=" * 62)
    print(f"VERIFIKASI vs DAFTAR RESMI CTID ({len(OFFICIAL)} file)")
    print("=" * 62)
    print(f"\n[1] File .mjson lokal: {len(mjson)} (slug unik: {len(local)})")

    match = set(local) & OFFICIAL
    extra = set(local) - OFFICIAL
    missing = OFFICIAL - set(local)

    print(f"\n[2] Cocok dengan daftar resmi : {len(match)} / {len(OFFICIAL)}")

    print(f"\n[3] Ada di lokal, TIDAK ada di daftar resmi: {len(extra)}")
    for s in sorted(extra):
        print(f"    {local[s]}")

    print(f"\n[4] Ada di daftar resmi, TIDAK ada di lokal: {len(missing)}")
    for s in sorted(missing):
        print(f"    (slug) {s[:70]}")

    print(f"\n[5] File non-.mjson di folder: {len(others)}")
    for f in others:
        try:
            size = f.stat().st_size
        except OSError:
            size = -1
        print(f"    {f.name}  ({size} byte)")

    print("\nSelesai. Salin seluruh output ini ke Claude.")

if __name__ == "__main__":
    main()