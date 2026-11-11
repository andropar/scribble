import AppKit

@main
enum ScribbleMain {
    @MainActor private static var delegate: AppDelegate?

    static func main() {
        let args = Array(CommandLine.arguments.dropFirst())
        if let first = args.first, first.hasPrefix("--") {
            CLI.run(args)
        }
        MainActor.assumeIsolated {
            let app = NSApplication.shared
            let delegate = AppDelegate()
            Self.delegate = delegate
            app.delegate = delegate
            app.setActivationPolicy(.accessory)
            app.run()
        }
    }
}
