import Foundation
import UIKit
import Photos
import Capacitor

/// Writes a finished photo straight to the camera roll.
///
/// The web build cannot do this at all — no browser API reaches Photos, which is why Save there
/// opens the system share sheet and asks the user to pick "Save Image". Inside the native shell
/// that restriction is gone, so Save can mean Save.
///
/// Add-only authorisation is deliberate: the app never reads the library, and asking for full
/// access to write one file is a permission prompt that scares people for no reason. `.addOnly`
/// also cannot be downgraded to "limited" in a way that breaks us — limited counts as granted for
/// adding.
@objc(SavePhotoPlugin)
public class SavePhotoPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "SavePhotoPlugin"
    public let jsName = "SavePhoto"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "save", returnType: CAPPluginReturnPromise)
    ]

    @objc func save(_ call: CAPPluginCall) {
        // Base64 rather than a file path: the JS side already has the filtered pixels as a blob in
        // memory and never writes them to disk, so handing over a path would mean inventing a
        // temporary file purely to delete it again.
        guard let encoded = call.getString("data"),
              let data = Data(base64Encoded: encoded),
              let image = UIImage(data: data) else {
            call.reject("The image could not be read")
            return
        }

        PHPhotoLibrary.requestAuthorization(for: .addOnly) { status in
            guard status == .authorized || status == .limited else {
                // Rejected rather than resolved: the caller falls back to the share sheet, which
                // can still save without this permission. Silently doing nothing would look like
                // the button was broken.
                call.reject("No permission to add to Photos")
                return
            }
            PHPhotoLibrary.shared().performChanges {
                PHAssetChangeRequest.creationRequestForAsset(from: image)
            } completionHandler: { success, error in
                if success {
                    call.resolve()
                } else {
                    call.reject(error?.localizedDescription ?? "The photo could not be saved")
                }
            }
        }
    }
}
