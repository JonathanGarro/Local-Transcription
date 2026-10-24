import Foundation
import FoundationModels

// apple intelligence for the python server, which can't reach it. answers in json on stdout:
//   BoswellAI check     whether the model can be used, {"available": true} or {"available": false, "reason": "..."}
//   BoswellAI todos     reads {"instructions": "...", "prompts": ["...", ...]} on stdin and finds the to-dos in each
//                       prompt, each in a session of its own, as {"results": [{"todos": [...]}, ...]}, each with
//                       the stamp of its line rather than seconds. a prompt
//                       too long for the model comes back as {"too_long": true}, to be split and sent again

@Generable
struct FoundTodo {
    @Guide(description: "A short imperative that starts with a verb, about something said in the text")
    var title: String
    @Guide(description: "Who asked for it, or that they offered, and the detail needed to act on it, in a sentence or two")
    var notes: String
    @Guide(description: "The meeting id it came from, or an empty string")
    var meetingId: String
    // the model copies a stamp more reliably than it works out seconds, so python does that
    @Guide(description: "The stamp of the line where it was asked or offered, copied from its [m:ss], like 12:05, or an empty string")
    var stamp: String
    @Guide(description: "The id of the project that fits best, or an empty string")
    var projectId: String
    @Guide(description: "\"today\", \"tomorrow\", a YYYY-MM-DD date, or an empty string")
    var when: String
    @Guide(description: "A YYYY-MM-DD date, or an empty string")
    var deadline: String
}

@Generable
struct FoundTodos {
    var todos: [FoundTodo]
}

struct Request: Decodable {
    let instructions: String
    let prompts: [String]
}

func reply(_ value: Any) {
    let data = (try? JSONSerialization.data(withJSONObject: value)) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

func unavailable() -> String? {
    switch SystemLanguageModel.default.availability {
    case .available:
        return nil
    case .unavailable(.deviceNotEligible):
        return "This Mac can't run Apple Intelligence."
    case .unavailable(.appleIntelligenceNotEnabled):
        return "Apple Intelligence is off. Turn it on in System Settings, Apple Intelligence & Siri."
    case .unavailable(.modelNotReady):
        return "Apple Intelligence is still downloading its model. Try again later."
    case .unavailable(let other):
        return "Apple Intelligence isn't available (\(other))."
    }
}

func todos(_ request: Request) async -> [[String: Any]] {
    var results: [[String: Any]] = []
    for prompt in request.prompts {
        // a fresh session per prompt, so earlier parts don't use up the context
        let session = LanguageModelSession(instructions: request.instructions)
        do {
            let found = try await session.respond(to: prompt, generating: FoundTodos.self,
                                                  options: GenerationOptions(samplingMode: .greedy)).content
            results.append(["todos": found.todos.map { t in
                ["title": t.title, "notes": t.notes, "meeting_id": t.meetingId, "stamp": t.stamp,
                 "project_id": t.projectId, "when": t.when, "deadline": t.deadline] as [String: Any]
            }])
        } catch LanguageModelSession.GenerationError.exceededContextWindowSize {
            results.append(["too_long": true])
        } catch {
            // a part the model won't read, such as one its guardrails stop, is left out
            results.append(["todos": [], "skipped": String(describing: error)])
        }
    }
    return results
}

switch CommandLine.arguments.dropFirst().first {
case "check":
    if let reason = unavailable() {
        reply(["available": false, "reason": reason])
    } else {
        reply(["available": true])
    }
case "todos":
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard let request = try? JSONDecoder().decode(Request.self, from: input) else {
        reply(["error": "expected {\"instructions\": ..., \"prompts\": [...]} on stdin"])
        exit(1)
    }
    if let reason = unavailable() {
        reply(["error": reason])
        exit(1)
    }
    reply(["results": await todos(request)])
default:
    reply(["error": "usage: BoswellAI check | todos"])
    exit(1)
}
