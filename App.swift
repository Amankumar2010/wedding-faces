import SwiftUI
import AppKit
import Combine

let base = Bundle.main.bundleURL.deletingLastPathComponent()
struct Person: Codable, Identifiable, Hashable { let id:Int; let name:String; let count:Int; let thumb:String; var label:String { name.isEmpty ? "Person \(id)" : name } }
struct MergeSuggestion:Codable,Identifiable {let id:String;let keep:Int;let source:Int;let leftName:String;let rightName:String;let leftCount:Int;let rightCount:Int;let leftThumbs:[String];let rightThumbs:[String]}
struct ReviewSummary:Codable {let items:[MergeSuggestion];let remaining:Int}
struct BatchCandidate:Codable,Identifiable {let id:Int;let count:Int;let thumbs:[String]}
struct PersonBatch:Codable,Identifiable {let id:Int;let name:String;let count:Int;let thumbs:[String];let items:[BatchCandidate]}
struct BatchSummary:Codable {let batches:[PersonBatch];let remaining:Int;let people:Int}
struct Photo: Codable, Identifiable { let id:Int; let face:String; let thumb:String; let jpg:String?; let raw:String?; let key:String }
struct Snapshot: Codable { var phase:String = "ready"; var message:String = "Loading library…"; var photos:Int = 0; var faces:Int = 0; var failed:Int = 0; var root:String = ""; var people:[Person] = []; var done:Int?; var total:Int?; var skipped:Int?; var seconds:Double? }
func engine(_ args:[String]) throws -> Data {
    let p=Process(); let out=Pipe()
    let errorURL=FileManager.default.temporaryDirectory.appendingPathComponent("wedding-faces-\(UUID().uuidString).log")
    FileManager.default.createFile(atPath:errorURL.path,contents:nil)
    let errorFile=try FileHandle(forWritingTo:errorURL)
    defer {try? errorFile.close();try? FileManager.default.removeItem(at:errorURL)}
    p.executableURL=base.appendingPathComponent(".venv/bin/python3")
    p.arguments=[base.appendingPathComponent("engine.py").path]+args
    p.standardOutput=out; p.standardError=errorFile
    p.standardInput=FileHandle.nullDevice
    p.qualityOfService = .userInitiated
    try p.run()
    try? out.fileHandleForWriting.close()
    let data=out.fileHandleForReading.readDataToEndOfFile(); p.waitUntilExit()
    if p.terminationStatus != 0 {
        let obj=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any]
        let errorText=(try? String(contentsOf:errorURL,encoding:.utf8)) ?? ""
        throw NSError(domain:"WeddingFaces",code:1,userInfo:[NSLocalizedDescriptionKey:obj?["error"] as? String ?? errorText])
    }
    return data
}
@MainActor final class Model:ObservableObject {
    @Published var groupFilter="People"
    @Published var reviewing=false
    @Published var reviewLoading=false
    @Published var suggestions:[MergeSuggestion]=[]
    @Published var reviewRemaining=0
    @Published var batchReviewing=false
    @Published var batchLoading=false
    @Published var batches:[PersonBatch]=[]
    @Published var batchRemaining=0
    @Published var batchPeople=0
    @Published var chosenBatch=0
    @Published var batchSelected:Set<Int>=[]
    @Published var previewing=false
    @Published var previewLoading=false
    @Published var previewImage:NSImage?=nil
    @Published var previewTitle=""
    @Published var previewError=""
    @Published var naming=false
    @Published var newName=""
    @Published var merging=false
    @Published var mergeTarget=0
    @Published var snapshot=Snapshot()
    @Published var root=""
    @Published var selected:Int?=nil
    @Published var photos:[Photo]=[]
    @Published var search=""
    @Published var notice=""
    @Published var busy=false
    @Published var loading=false
    @Published var offset=0
    private var refreshing=false
    private var scanProcess:Process?
    var scanning:Bool { snapshot.phase=="scanning" || scanProcess?.isRunning == true }
    var person:Person? { snapshot.people.first {$0.id==selected} }
    var filtered:[Person] { snapshot.people.filter {
        let matches=search.isEmpty || $0.label.localizedCaseInsensitiveContains(search)
        let visible = !search.isEmpty || groupFilter == "All" || (groupFilter == "People" ? $0.count>=5 || !$0.name.isEmpty : $0.count<5 && $0.name.isEmpty)
        return matches && visible
    } }
    func refresh() async {
        guard !refreshing else{return};refreshing=true;defer{refreshing=false}
        do {
            let data=try await Task.detached {try engine(["summary"])}.value
            snapshot=try JSONDecoder().decode(Snapshot.self,from:data)
            if !snapshot.root.isEmpty {root=snapshot.root}
        } catch { notice=error.localizedDescription }
    }
    func loadSuggestions() async {
        reviewLoading=true;defer{reviewLoading=false}
        do {let data=try await Task.detached{try engine(["review-summary"])}.value;let result=try JSONDecoder().decode(ReviewSummary.self,from:data);suggestions=result.items;reviewRemaining=result.remaining}
        catch {notice=error.localizedDescription}
    }
    func reviewAction(_ args:[String]) async {
        guard !busy && !reviewLoading else{return}
        busy=true;defer{busy=false}
        do {
            _ = try await Task.detached{try engine(args)}.value
            await refresh();await loadPhotos();await loadSuggestions()
        } catch {notice=error.localizedDescription}
    }
    func loadBatches() async {
        guard !batchLoading else{return}
        let previousSelection=Set(batches.flatMap{batch in batch.items.filter{batchSelected.contains($0.id)}.map{"\(batch.id):\($0.id)"}})
        batchLoading=true;defer{batchLoading=false}
        do {
            let data=try await Task.detached{try engine(["batch-summary"])}.value
            let result=try JSONDecoder().decode(BatchSummary.self,from:data)
            batches=result.batches;batchRemaining=result.remaining;batchPeople=result.people
            batchSelected=Set(result.batches.flatMap{batch in batch.items.filter{previousSelection.contains("\(batch.id):\($0.id)")}.map(\.id)})
        } catch {notice=error.localizedDescription}
    }
    func batchAction(_ args:[String]) async {
        guard !busy && !batchLoading else{return}
        busy=true;defer{busy=false}
        do {
            _ = try await Task.detached{try engine(args)}.value
            await refresh();await loadPhotos();await loadBatches()
        } catch {notice=error.localizedDescription}
    }
    func chooseRoot() {
        let panel=NSOpenPanel();panel.canChooseDirectories=true;panel.canChooseFiles=false;panel.prompt="Select Wedding Folder"
        if panel.runModal() == .OK, let url=panel.url {
            if snapshot.photos>0 && url.path != root {notice="This library is linked to \(root). Please use the same folder to resume.";return}
            root=url.path
        }
    }
    func scan(_ limit:Int) {
        guard !scanning else{return}
        guard !root.isEmpty else {notice="Choose a photo folder first.";return}
        do {
            let p=Process();p.executableURL=base.appendingPathComponent(".venv/bin/python3");p.arguments=[base.appendingPathComponent("engine.py").path,"scan",root,String(limit)]
            let log=base.appendingPathComponent("Library/scan.log");FileManager.default.createFile(atPath:log.path,contents:nil)
            let file=try FileHandle(forWritingTo:log);p.standardOutput=file;p.standardError=file
            try p.run();scanProcess=p;snapshot.phase="scanning";snapshot.message="Starting scan…"
        } catch {notice=error.localizedDescription}
    }
    func command(_ args:[String]) async {
        busy=true;defer{busy=false}
        do {
            let data=try await Task.detached {try engine(args)}.value
            if let obj=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any],let msg=obj["message"] as? String {notice=msg}
            await refresh();await loadPhotos()
        } catch {notice=error.localizedDescription}
    }
    func loadPhotos() async {
        guard let id=selected else {photos=[];return}
        let page=offset;loading=true;defer{loading=false}
        do {
            let data=try await Task.detached {try engine(["photos",String(id),String(page)])}.value
            let result=try JSONDecoder().decode([Photo].self,from:data)
            if selected==id && offset==page {photos=result}
        } catch {notice=error.localizedDescription}
    }
    func preview(_ photo:Photo) {
        guard !previewLoading else{return}
        previewTitle=URL(fileURLWithPath:photo.key).lastPathComponent
        previewImage=nil;previewError="";previewLoading=true;previewing=true
        Task {
            defer{previewLoading=false}
            do {
                let id=photo.id
                let data=try await Task.detached {try engine(["preview",String(id)])}.value
                guard let obj=try JSONSerialization.jsonObject(with:data) as? [String:String],
                      let path=obj["path"],let image=NSImage(contentsOfFile:path) else {
                    throw NSError(domain:"WeddingFaces",code:2,userInfo:[NSLocalizedDescriptionKey:"Unable to decode photo preview."])
                }
                previewImage=image
            } catch {previewError=error.localizedDescription}
        }
    }
    func export(_ mode:String) {
        guard let id=selected else{return}
        let panel=NSOpenPanel();panel.canChooseDirectories=true;panel.canChooseFiles=false;panel.canCreateDirectories=true;panel.prompt="Export Here";panel.message="Copies originals into a person folder. JPG + RAW exports can use substantial space."
        if panel.runModal() == .OK, let url=panel.url {Task {await command(["export",String(id),url.path,mode])}}
    }
}
struct Thumb:View {
    let path:String
    var body:some View {
        if let img=NSImage(contentsOfFile:path) {GeometryReader {geometry in Image(nsImage:img).resizable().scaledToFill().frame(width:geometry.size.width,height:geometry.size.height).clipped()}}
        else {Rectangle().fill(Color.gray.opacity(0.15)).overlay(Image(systemName:"photo"))}
    }
}
struct ContentView:View {
    @StateObject var m=Model()
    let timer=Timer.publish(every:3,on:.main,in:.common).autoconnect()
    var body:some View {
        NavigationSplitView {
            VStack(alignment:.leading,spacing:16) {
                HStack {Image(systemName:"person.2.crop.square.stack.fill").font(.title).foregroundStyle(.orange);VStack(alignment:.leading){Text("Wedding Faces").font(.title3.bold());Text("Your people. Your memories.").font(.caption).foregroundStyle(.secondary)}}.padding(.top,16)
                TextField("Find a person",text:$m.search).textFieldStyle(.roundedBorder)
                Picker("Group size",selection:$m.groupFilter) {Text("People").tag("People");Text("Small groups").tag("Small");Text("All").tag("All")}.pickerStyle(.segmented)
                Text("\(m.filtered.count) GROUPS SHOWN").font(.caption.bold()).foregroundStyle(.secondary)
                Text("Group counts include uncertain face fragments. Review suggestions show the matches left to check.").font(.caption2).foregroundStyle(.secondary)
                List(m.filtered,selection:$m.selected) {person in
                    HStack(spacing:12) {Thumb(path:person.thumb).frame(width:46,height:46).clipShape(RoundedRectangle(cornerRadius:12));VStack(alignment:.leading,spacing:4){Text(person.label).fontWeight(.medium);Text("\(person.count) photos").font(.caption).foregroundStyle(.secondary)}}.padding(.vertical,4).tag(person.id)
                }.listStyle(.sidebar)
                Label("Processed on this Mac",systemImage:"lock.shield").font(.caption).foregroundStyle(.secondary).padding(.bottom,14)
            }.padding(.horizontal,14).navigationSplitViewColumnWidth(min:250,ideal:280)
        } detail: {
            VStack(alignment:.leading,spacing:0) {
                header
                Divider()
                if let p=m.person {personView(p)} else {welcome}
                Spacer(minLength:0)
                Divider()
                HStack {Circle().fill(m.scanning ? .orange : .green).frame(width:7,height:7);Text(m.snapshot.message).lineLimit(1);Spacer();if m.busy || m.loading {ProgressView().controlSize(.small)};if m.snapshot.failed>0 {Button("\(m.snapshot.failed) errors") {Task{do{let d=try await Task.detached{try engine(["errors"])}.value;m.notice=String(data:d,encoding:.utf8) ?? ""}catch{m.notice=error.localizedDescription}}}}}.font(.caption).foregroundStyle(.secondary).padding(14)
            }.background(Color(nsColor:.windowBackgroundColor))
        }
        .frame(minWidth:1020,minHeight:720)
        .task {await m.refresh()}
        .onReceive(timer) {_ in Task{await m.refresh()}}
        .onChange(of:m.selected) {_,_ in m.offset=0;Task{await m.loadPhotos()}}
        .alert("Wedding Faces",isPresented:Binding(get:{!m.notice.isEmpty},set:{if !$0{m.notice=""}})) {Button("OK"){m.notice=""}} message:{Text(m.notice)}
        .sheet(isPresented:$m.reviewing) {mergeReview}
        .sheet(isPresented:$m.batchReviewing) {batchReview}
        .sheet(isPresented:$m.previewing) {
            VStack(spacing:16) {
                HStack {Text(m.previewTitle).font(.headline);Spacer();Button("Close"){m.previewing=false}.keyboardShortcut(.cancelAction)}
                if m.previewLoading {ProgressView("Loading photo from SSD…").frame(maxWidth:.infinity,maxHeight:.infinity)}
                else if let img=m.previewImage {Image(nsImage:img).resizable().scaledToFit().frame(maxWidth:.infinity,maxHeight:.infinity)}
                else {Text(m.previewError).foregroundStyle(.secondary).frame(maxWidth:.infinity,maxHeight:.infinity)}
                Text("Photo preview • Original file preserved on your SSD").font(.caption).foregroundStyle(.secondary)
            }.padding(22).frame(width:960,height:680)
        }
        .sheet(isPresented:$m.naming) {VStack(alignment:.leading,spacing:18){Text("Name this person").font(.title2.bold());TextField("Name",text:$m.newName).textFieldStyle(.roundedBorder);HStack{Button("Cancel"){m.naming=false};Spacer();Button("Save"){if let id=m.selected{Task{await m.command(["rename",String(id),m.newName])}};m.naming=false}.keyboardShortcut(.defaultAction)}}.padding(28).frame(width:350)}
        .sheet(isPresented:$m.merging) {VStack(alignment:.leading,spacing:18){Text("Merge into another group").font(.title2.bold());Text("Use this when both groups show the same person. Individual mistakes can be moved out afterward.").foregroundStyle(.secondary);Picker("Keep this group",selection:$m.mergeTarget){Text("Choose a person").tag(0);ForEach(m.snapshot.people.filter{$0.id != m.selected}){Text("\($0.label) (\($0.count))").tag($0.id)}};HStack{Button("Cancel"){m.merging=false};Spacer();Button("Merge"){if let id=m.selected{let dst=m.mergeTarget;Task{await m.command(["merge",String(id),String(dst)]);m.selected=dst}};m.merging=false}.disabled(m.mergeTarget==0)}}.padding(28).frame(width:440)}
    }
    var batchReview:some View {BatchReviewView(m:m)}
    var mergeReview:some View {
        VStack(alignment:.leading,spacing:16) {
            HStack {Text("Are these the same person?").font(.title2.bold());Spacer();Button("Done"){m.reviewing=false}.keyboardShortcut(.cancelAction)}
            Text("Compare faces before merging. Different people and Skip are saved, including after restarting the app.").foregroundStyle(.secondary)
            if !m.reviewLoading {
                HStack {Text("\(m.reviewRemaining.formatted()) suggested pairs remaining").font(.headline);Spacer();Text("Showing \(m.suggestions.count)").foregroundStyle(.secondary)}
                Text("Excludes skipped and rejected pairs. Recalculated after each decision; merging groups can change the total.").font(.caption).foregroundStyle(.secondary)
            }
            if m.reviewLoading {ProgressView("Finding similar groups…").frame(maxWidth:.infinity,maxHeight:.infinity)}
            else if m.suggestions.isEmpty {Text("No unreviewed suggestions remain. Use Show skipped to revisit deferred pairs.").frame(maxWidth:.infinity,maxHeight:.infinity)}
            else {
                ScrollView {LazyVStack(spacing:20) {ForEach(m.suggestions) {item in
                    VStack(alignment:.leading,spacing:12) {
                        HStack(alignment:.top,spacing:24) {
                            VStack(alignment:.leading) {Text("\(item.leftName) · \(item.leftCount) photos").font(.headline);HStack{ForEach(item.leftThumbs,id:\.self){Thumb(path:$0).frame(width:76,height:76).clipped().cornerRadius(10)}}}.frame(maxWidth:.infinity,alignment:.leading)
                            VStack(alignment:.leading) {Text("\(item.rightName) · \(item.rightCount) photos").font(.headline);HStack{ForEach(item.rightThumbs,id:\.self){Thumb(path:$0).frame(width:76,height:76).clipped().cornerRadius(10)}}}.frame(maxWidth:.infinity,alignment:.leading)
                        }
                        HStack {Button("Same person — merge") {Task{await m.reviewAction(["merge",String(item.source),String(item.keep)])}}.buttonStyle(.borderedProminent).disabled(m.busy);Button("Different people"){Task{await m.reviewAction(["review-decision",String(item.keep),String(item.source),"different"])}}.disabled(m.busy || m.reviewLoading);Button("Skip"){Task{await m.reviewAction(["review-decision",String(item.keep),String(item.source),"skipped"])}}.disabled(m.busy || m.reviewLoading)}
                        Divider()
                    }
                }}}
            }
            HStack {Button("Undo last skip / different decision"){Task{await m.reviewAction(["review-undo"])}};Button("Show skipped again"){Task{await m.reviewAction(["review-reset-skipped"])}}}.disabled(m.busy || m.reviewLoading)
            Text("People shows groups with 5+ photos and named groups. Smaller groups remain accessible in the sidebar.").font(.caption).foregroundStyle(.secondary)
        }.padding(24).frame(width:780,height:660).task{ }
    }
    var header:some View {
        VStack(alignment:.leading,spacing:12) {
            HStack {VStack(alignment:.leading,spacing:4){Text("The wedding collection").font(.largeTitle.bold());Text("\(m.snapshot.photos.formatted()) photos indexed  ·  \(m.snapshot.faces.formatted()) faces found").foregroundStyle(.secondary)};Spacer();if m.scanning{Button("Pause scan",systemImage:"pause.fill"){Task{await m.command(["pause"])}}}} 
            HStack {Image(systemName:"externaldrive");Text(m.root).font(.caption).lineLimit(1).truncationMode(.middle);Spacer();Button("Choose folder"){m.chooseRoot()}.disabled(m.scanning || m.busy)}
            HStack {Button("Improve grouping"){Task{await m.command(["smart-regroup"])}};Button("Batch review"){m.batchReviewing=true;Task{await m.loadBatches()}};Button("Review pairs") {m.reviewing=true;Task{await m.loadSuggestions()}};Menu("Library") {Button("Undo automatic regrouping"){Task{await m.command(["undo-regroup"])}}};if m.busy{ProgressView().controlSize(.small);Text("Updating groups…").font(.caption)}}.disabled(m.scanning || m.busy)
            HStack {Button("Scan 100-photo sample"){m.scan(100)}.disabled(m.scanning || m.busy || m.root.isEmpty);Button(m.snapshot.photos>0 ? "Resume / scan all" : "Scan all photos"){m.scan(0)}.buttonStyle(.borderedProminent).tint(.orange).disabled(m.scanning || m.busy || m.root.isEmpty);Spacer();if m.scanning {Text("\((m.snapshot.skipped ?? 0)+(m.snapshot.done ?? 0)) / \(m.snapshot.total ?? 0)").font(.caption.monospacedDigit());ProgressView().controlSize(.small)}}
        }.padding(26)
    }
    var welcome:some View {
        VStack(spacing:20) {
            Image(systemName:"person.crop.rectangle.stack").font(.system(size:62,weight:.light)).foregroundStyle(.orange)
            Text(m.snapshot.people.isEmpty ? "Find everyone in your wedding photos" : "Choose a face to explore their photos").font(.title2.bold())
            Text("JPG and ARW pairs appear as one photo.\nName suggested groups, correct matches, then export originals.").multilineTextAlignment(.center).foregroundStyle(.secondary)
            HStack(spacing:30) {Label("Offline",systemImage:"wifi.slash");Label("Resumable",systemImage:"arrow.clockwise");Label("Originals preserved",systemImage:"externaldrive.badge.checkmark")}.font(.caption)
            Text("Start with a sample to review matching quality. Pause scanning before editing groups.").font(.caption).foregroundStyle(.secondary)
        }.frame(maxWidth:.infinity,maxHeight:.infinity).padding(32)
    }
    func personView(_ p:Person)->some View {
        VStack(alignment:.leading,spacing:16) {
            HStack {Text(p.label).font(.title.bold());Text("\(p.count) photos").foregroundStyle(.secondary);Spacer();Button("Name"){m.newName=p.name;m.naming=true}.disabled(m.scanning || m.busy);Button("Merge…"){m.mergeTarget=0;m.merging=true}.disabled(m.scanning || m.busy);Menu("Export copies") {Button("JPG only"){m.export("jpg")};Button("ARW only"){m.export("raw")};Button("JPG + ARW"){m.export("both")}}.disabled(m.scanning || m.busy)}
            Text("Suggested matches • Click a photo to preview it. Right-click to reveal the original or move an incorrect face into a new group.").font(.caption).foregroundStyle(.secondary)
            ScrollView {LazyVGrid(columns:[GridItem(.adaptive(minimum:190),spacing:16)],spacing:18) {ForEach(m.photos){photo in
                VStack(alignment:.leading,spacing:8){ZStack(alignment:.bottomTrailing){Thumb(path:photo.thumb).frame(height:155).clipped().clipShape(RoundedRectangle(cornerRadius:12));Thumb(path:photo.face).frame(width:42,height:42).clipped().clipShape(RoundedRectangle(cornerRadius:8)).overlay(RoundedRectangle(cornerRadius:8).stroke(.white,lineWidth:2)).padding(8)};HStack{Text(URL(fileURLWithPath:photo.key).lastPathComponent).font(.caption).lineLimit(1);Spacer();Text(photo.jpg != nil && photo.raw != nil ? "JPG + RAW" : photo.raw != nil ? "RAW" : "JPG").font(.system(size:9,weight:.semibold)).foregroundStyle(.secondary)}}
                .onTapGesture{m.preview(photo)}
                .contextMenu {Button("Reveal original in Finder"){if let path=photo.jpg ?? photo.raw {NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath:path)])}};Button("Move this face to a new group"){Task{await m.command(["detach",String(photo.id)])}}.disabled(m.scanning || m.busy)}
            }}}
            HStack{Button("Previous"){m.offset=max(0,m.offset-300);Task{await m.loadPhotos()}}.disabled(m.offset==0 || m.loading);Text("Page \(m.offset/300+1)").font(.caption);Button("Next"){m.offset+=300;Task{await m.loadPhotos()}}.disabled(m.photos.count<300 || m.loading);Spacer()}
        }.padding(26)
    }
}
@main struct WeddingFacesApp:App {
    var body:some Scene {WindowGroup {ContentView()}.defaultSize(width:1200,height:800)}
}

struct BatchReviewView:View {
    @ObservedObject var m:Model
    var current:PersonBatch? {m.batches.first{$0.id==m.chosenBatch} ?? m.batches.first}
    var body:some View {
        VStack(alignment:.leading,spacing:16) {
            HStack {Text("Review one person at a time").font(.title2.bold());Spacer();Button("Done"){m.batchReviewing=false}.keyboardShortcut(.cancelAction)}
            Text("Select the groups that match the reference person, then merge them together. Nothing is selected automatically.").foregroundStyle(.secondary)
            Text("\(m.batchRemaining.formatted()) suggested groups across \(m.batchPeople.formatted()) people").font(.headline)
            if m.batchLoading {ProgressView("Comparing different views of each person…").frame(maxWidth:.infinity,maxHeight:.infinity)}
            else if m.batches.isEmpty {Text("No batch suggestions remain. Review pairs can show additional uncertain matches.").frame(maxWidth:.infinity,maxHeight:.infinity)}
            else if let batch=current {
                Picker("Review person",selection:Binding(get:{batch.id},set:{m.chosenBatch=$0;m.batchSelected=[]})) {
                    ForEach(m.batches){person in Text("\(person.name) · \(person.items.count) possible groups").tag(person.id)}
                }.disabled(m.busy || m.batchLoading)
                BatchPersonView(m:m,batch:batch).id(batch.id)
            }
            if !m.notice.isEmpty {Text(m.notice).font(.caption).foregroundStyle(.secondary).lineLimit(3)}
            HStack {Button("Undo last skip / different decision"){Task{await m.batchAction(["review-undo"])}};Button("Show skipped again"){Task{await m.batchAction(["review-reset-skipped"])}}}.disabled(m.busy || m.batchLoading)
            Text("Suggestions are uncertain matches. Skipped groups stay saved; remaining counts change after merging.").font(.caption).foregroundStyle(.secondary)
        }.padding(24).frame(width:880,height:720)
    }
}
struct BatchPersonView:View {
    @ObservedObject var m:Model
    let batch:PersonBatch
    var selected:[Int] {batch.items.map(\.id).filter{m.batchSelected.contains($0)}}
    var body:some View {
        VStack(alignment:.leading,spacing:14) {
            HStack {
                VStack(alignment:.leading) {Text(batch.name).font(.title3.bold());Text("\(batch.count) photos · \(batch.items.count) possible groups").foregroundStyle(.secondary)}
                Spacer()
                ForEach(batch.thumbs,id:\.self){Thumb(path:$0).frame(width:76,height:76).clipped().cornerRadius(10)}
            }
            HStack(spacing:12) {
                Button("Select all") {m.batchSelected.formUnion(batch.items.map(\.id))}.disabled(selected.count==batch.items.count)
                Button("Clear selection") {m.batchSelected.subtract(batch.items.map(\.id))}.disabled(selected.isEmpty)
                Spacer()
                Text("\(selected.count) of \(batch.items.count) selected").font(.caption).foregroundStyle(.secondary)
            }
            ScrollView {LazyVGrid(columns:[GridItem(.flexible()),GridItem(.flexible())],alignment:.leading,spacing:12) {
                ForEach(batch.items) {item in
                    VStack(alignment:.leading,spacing:8) {
                        Toggle("Person \(item.id) · \(item.count) photos",isOn:Binding(get:{m.batchSelected.contains(item.id)},set:{if $0{m.batchSelected.insert(item.id)}else{m.batchSelected.remove(item.id)}}))
                        HStack {ForEach(item.thumbs,id:\.self){Thumb(path:$0).frame(width:62,height:62).clipped().cornerRadius(8)}}
                        HStack {
                            Button("Different person"){Task{await m.batchAction(["review-decision",String(batch.id),String(item.id),"different"])}}
                            Button("Skip"){Task{await m.batchAction(["review-decision",String(batch.id),String(item.id),"skipped"])}}
                        }.font(.caption)
                    }.padding(12).frame(maxWidth:.infinity,alignment:.leading).background(Color.secondary.opacity(0.07)).cornerRadius(12)
                }
            }}
            Button("Merge \(selected.count) selected groups into \(batch.name)") {
                let args=["merge-batch",String(batch.id)]+selected.map{String($0)}
                Task{await m.batchAction(args)}
            }.buttonStyle(.borderedProminent).disabled(selected.isEmpty)
        }.disabled(m.busy || m.batchLoading)
    }
}
