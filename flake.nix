{
  description = "pixi env";
  inputs = {
    flake-utils.url = "github:numtide/flake-utils";
  };
  outputs =
    { flake-utils, nixpkgs, ... }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        fhs = pkgs.buildFHSEnv {
          name = "pixi-env";

          targetPkgs = _: with pkgs; [ pixi glib alsa-lib alsa-utils ];
        };
      in
      {
        devShell = fhs.env;
      }
    );
    
}
