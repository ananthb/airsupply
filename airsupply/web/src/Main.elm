port module Main exposing (main)

{-| The airsupply page.

Elm owns the state and the view, pure; js/api.js owns the add-on's HTTP API and
the polling. Intents go out `toAddon`, and the add-on's whole state comes back
on `fromAddon` -- there is nothing here to merge and so nothing to go stale.

The page has one subject, the machine, and one job: say whether airsupply is
talking to it and show what it last read. Setting it up is a thing that
happens once, so it only takes up room while it is unfinished.
-}

import Browser
import Html exposing (..)
import Html.Attributes exposing (..)
import Html.Events exposing (onClick, onInput, onSubmit)
import Json.Decode as D
import Json.Encode as E



-- PORTS


port toAddon : E.Value -> Cmd msg


port fromAddon : (D.Value -> msg) -> Sub msg



-- MODEL


type Stage
    = NoAdapter
    | Choose
    | NeedsPairing
    | NeedsPin
    | Ready


type alias Machine =
    { address : String
    , name : String
    , signal : Maybe Int
    , classic : Bool
    , serial : Bool
    , bonded : Bool
    , candidate : Bool
    }


type alias Field =
    { label : String, value : String }


type alias Section =
    { title : String, ok : Bool, problem : Maybe String, fields : List Field }


type alias Reading =
    { at : String, sections : List Section }


type alias Question =
    { kind : String, code : Maybe String }


type alias Line =
    { at : String, level : String, text : String }


type alias State =
    { version : String
    , hasAdapter : Bool
    , stage : Stage
    , busy : Maybe String
    , scanning : Bool
    , machines : List Machine
    , machine : Maybe Machine
    , question : Maybe Question
    , problem : Maybe String
    , reading : Maybe Reading
    , log : List Line
    }


type alias Model =
    { state : Maybe State
    , unreachable : Maybe String
    , pin : String
    , reply : String
    , picking : Bool
    }


init : () -> ( Model, Cmd Msg )
init _ =
    ( { state = Nothing, unreachable = Nothing, pin = "", reply = "", picking = False }
    , Cmd.none
    )



-- UPDATE


type Msg
    = Incoming D.Value
    | Send String (List ( String, E.Value ))
    | PinTyped String
    | ReplyTyped String
    | SendPin
    | SendReply
    | TogglePicking


update : Msg -> Model -> ( Model, Cmd Msg )
update msg model =
    case msg of
        Incoming raw ->
            ( absorb raw model, Cmd.none )

        Send kind extras ->
            ( model, toAddon (E.object (( "kind", E.string kind ) :: extras)) )

        PinTyped value ->
            ( { model | pin = digits 8 value }, Cmd.none )

        ReplyTyped value ->
            ( { model | reply = digits 8 value }, Cmd.none )

        SendPin ->
            ( model, toAddon (E.object [ ( "kind", E.string "pair" ), ( "pin", E.string model.pin ) ]) )

        SendReply ->
            ( model, toAddon (E.object [ ( "kind", E.string "answer" ), ( "value", E.string model.reply ), ( "accept", E.bool True ) ]) )

        TogglePicking ->
            ( { model | picking = not model.picking }, Cmd.none )


{-| Keep a typed code to digits. Both things a person types here -- the
machine's PIN and the Bluetooth code -- are numeric, and a stray space is a
failed handshake that looks like a wrong number.
-}
digits : Int -> String -> String
digits limit value =
    value |> String.filter Char.isDigit |> String.left limit


absorb : D.Value -> Model -> Model
absorb raw model =
    case D.decodeValue eventDecoder raw of
        Ok (Unreachable why) ->
            { model | unreachable = Just why }

        Ok (Fresh state) ->
            { model
                | state = Just state
                , unreachable = Nothing

                -- Clear a field once what it was for is over, so a code does
                -- not sit on screen after it has been accepted.
                , pin =
                    if state.stage == NeedsPin then
                        model.pin

                    else
                        ""
                , reply =
                    if state.question == Nothing then
                        ""

                    else
                        model.reply
                , picking =
                    if state.stage == Choose then
                        False

                    else
                        model.picking
            }

        Err err ->
            { model | unreachable = Just (D.errorToString err) }



-- DECODERS


type Event
    = Fresh State
    | Unreachable String


eventDecoder : D.Decoder Event
eventDecoder =
    D.field "kind" D.string
        |> D.andThen
            (\kind ->
                case kind of
                    "state" ->
                        D.map Fresh (D.field "state" stateDecoder)

                    _ ->
                        D.map Unreachable (D.field "message" D.string)
            )


stateDecoder : D.Decoder State
stateDecoder =
    let
        cons =
            D.map8 State
                (D.field "version" D.string)
                (D.field "has_adapter" D.bool)
                (D.field "stage" stageDecoder)
                (D.field "busy" (D.nullable D.string))
                (D.field "scanning" D.bool)
                (D.field "machines" (D.list machineDecoder))
                (D.field "machine" (D.nullable machineDecoder))
                (D.field "question" (D.nullable questionDecoder))
    in
    cons
        |> andMap (D.field "problem" (D.nullable D.string))
        |> andMap (D.field "reading" (D.nullable readingDecoder))
        |> andMap (D.field "log" (D.list lineDecoder))


andMap : D.Decoder a -> D.Decoder (a -> b) -> D.Decoder b
andMap =
    D.map2 (|>)


stageDecoder : D.Decoder Stage
stageDecoder =
    D.string
        |> D.map
            (\name ->
                case name of
                    "no-adapter" ->
                        NoAdapter

                    "choose" ->
                        Choose

                    "pairing" ->
                        NeedsPairing

                    "pin" ->
                        NeedsPin

                    _ ->
                        Ready
            )


machineDecoder : D.Decoder Machine
machineDecoder =
    D.map7 Machine
        (D.field "address" D.string)
        (D.field "name" D.string)
        (D.field "signal" (D.nullable D.int))
        (D.field "classic" D.bool)
        (D.field "serial" D.bool)
        (D.field "bonded" D.bool)
        (D.field "candidate" D.bool)


readingDecoder : D.Decoder Reading
readingDecoder =
    D.map2 Reading
        (D.field "at" D.string)
        (D.field "sections" (D.list sectionDecoder))


sectionDecoder : D.Decoder Section
sectionDecoder =
    D.map4 Section
        (D.field "title" D.string)
        (D.field "ok" D.bool)
        (D.field "problem" (D.nullable D.string))
        (D.field "fields" (D.list fieldDecoder))


fieldDecoder : D.Decoder Field
fieldDecoder =
    D.map2 Field (D.field "label" D.string) (D.field "value" D.string)


questionDecoder : D.Decoder Question
questionDecoder =
    D.map2 Question (D.field "kind" D.string) (D.field "code" (D.nullable D.string))


lineDecoder : D.Decoder Line
lineDecoder =
    D.map3 Line (D.field "at" D.string) (D.field "level" D.string) (D.field "text" D.string)



-- VIEW


view : Model -> Html Msg
view model =
    main_ []
        (case ( model.state, model.unreachable ) of
            ( Nothing, Nothing ) ->
                [ p [ class "muted" ] [ text "Starting." ] ]

            ( Nothing, Just why ) ->
                [ div [ class "banner" ] [ text ("The add-on is not answering. " ++ why) ] ]

            ( Just state, _ ) ->
                page model state
        )


page : Model -> State -> List (Html Msg)
page model state =
    List.concat
        [ [ heading state ]
        , maybeView model.unreachable (\why -> div [ class "banner" ] [ text ("The add-on is not answering. " ++ why) ])
        , maybeView state.problem (\why -> div [ class "banner" ] [ text why ])
        , maybeView state.question (question model state)
        , setup model state
        , readings state
        , [ activity state, footer state ]
        ]


maybeView : Maybe a -> (a -> Html Msg) -> List (Html Msg)
maybeView value render =
    case value of
        Just something ->
            [ render something ]

        Nothing ->
            []


heading : State -> Html Msg
heading state =
    div []
        [ div [ class "machine" ]
            (case state.machine of
                Just machine ->
                    [ h1 [] [ text (label machine) ], span [ class "addr" ] [ text machine.address ] ]

                Nothing ->
                    [ h1 [] [ text "No machine yet" ] ]
            )
        , div [ class "link" ]
            [ span [ class ("dot " ++ lampClass state) ] []
            , text (lampText state)
            ]
        ]


label : Machine -> String
label machine =
    if String.isEmpty machine.name then
        machine.address

    else
        machine.name


lampClass : State -> String
lampClass state =
    if state.busy /= Nothing || state.scanning then
        "work"

    else
        case state.stage of
            Ready ->
                "live"

            NoAdapter ->
                "none"

            _ ->
                "none"


lampText : State -> String
lampText state =
    case ( state.busy, state.scanning ) of
        ( Just "bond", _ ) ->
            "Pairing with Bluetooth."

        ( Just "pair", _ ) ->
            "Connecting to the machine."

        ( Just "read", _ ) ->
            "Reading."

        ( Just "forget", _ ) ->
            "Forgetting."

        ( Just other, _ ) ->
            other

        ( Nothing, True ) ->
            "Looking for machines."

        ( Nothing, False ) ->
            idleText state


idleText : State -> String
idleText state =
    case state.stage of
        NoAdapter ->
            "Home Assistant has no Bluetooth adapter."

        Choose ->
            "Not set up yet."

        NeedsPairing ->
            "Not paired with Bluetooth."

        NeedsPin ->
            "Paired. Waiting for the machine's PIN."

        Ready ->
            case state.reading of
                Just reading ->
                    "Connected. Read at " ++ reading.at ++ "."

                Nothing ->
                    "Connected. Nothing read yet."



-- SETUP


setup : Model -> State -> List (Html Msg)
setup model state =
    let
        working =
            state.busy /= Nothing
    in
    case state.stage of
        NoAdapter ->
            [ div [ class "card" ]
                [ p [ class "note" ]
                    [ text "Home Assistant cannot see a Bluetooth adapter, so nothing here can work. Check that the host has one and that the Bluetooth integration is running." ]
                ]
            ]

        Choose ->
            [ div [ class "card" ]
                [ p [ class "note" ] [ text "Put the AirMini in pairing mode, then look for it. It is only discoverable while it is." ]
                , div [ class "row-btns" ] [ scanButton state ]
                , machineTable state
                ]
            ]

        NeedsPairing ->
            [ div [ class "card" ]
                [ p [ class "note" ] [ text "Put the AirMini in pairing mode and pair within a few seconds of it lighting up. It only answers while it is in pairing mode." ]
                , div [ class "row-btns" ]
                    [ button [ class "go", onClick (Send "bond" []), disabled working ] [ text "Pair" ]
                    , scanButton state
                    , forgetButton working
                    ]
                ]
            ]

        NeedsPin ->
            [ div [ class "card" ]
                [ p [ class "note" ] [ text "Type the PIN on the machine's screen. Once only: airsupply keeps the key the machine gives back, and connects with that from then on." ]
                , Html.form [ class "row-btns", onSubmit SendPin ]
                    [ input
                        [ value model.pin
                        , onInput PinTyped
                        , inputmode "numeric"
                        , autocomplete False
                        , attribute "autofocus" ""
                        , placeholder "PIN"
                        , attribute "aria-label" "PIN on the machine's screen"
                        ]
                        []
                    , button [ class "go", disabled (working || String.isEmpty model.pin) ] [ text "Connect" ]
                    , forgetButton working
                    ]
                ]
            ]

        Ready ->
            [ div [ class "row-btns", style "margin-bottom" "16px" ]
                [ button [ class "go", onClick (Send "read" []), disabled working ] [ text "Read now" ]
                , button [ class "plain", onClick TogglePicking ]
                    [ text
                        (if model.picking then
                            "Close"

                         else
                            "Change machine"
                        )
                    ]
                ]
            , if model.picking then
                div [ class "card" ]
                    [ div [ class "row-btns" ] [ scanButton state, forgetButton working ]
                    , machineTable state
                    ]

              else
                text ""
            ]


scanButton : State -> Html Msg
scanButton state =
    button
        [ onClick (Send "scan" []), disabled (state.scanning || state.busy /= Nothing || not state.hasAdapter) ]
        [ text
            (if state.scanning then
                "Looking"

             else
                "Look for machines"
            )
        ]


forgetButton : Bool -> Html Msg
forgetButton working =
    button [ class "plain", onClick (Send "forget" []), disabled working ] [ text "Forget" ]


machineTable : State -> Html Msg
machineTable state =
    let
        candidates =
            List.filter .candidate state.machines

        others =
            List.filter (not << .candidate) state.machines
    in
    div []
        [ if List.isEmpty candidates then
            p [ class "note", style "margin-top" "12px" ]
                [ text "No AirMini found yet. If it is in pairing mode and still does not appear, Home Assistant is out of range." ]

          else
            table [] (List.map (machineRow state) candidates)
        , if List.isEmpty others then
            text ""

          else
            details []
                [ summary [] [ text (String.fromInt (List.length others) ++ " other Bluetooth devices") ]
                , table [] (List.map (machineRow state) others)
                ]
        ]


machineRow : State -> Machine -> Html Msg
machineRow state machine =
    let
        chosen =
            Just machine.address == Maybe.map .address state.machine
    in
    tr []
        [ td []
            [ div [ class "name" ] [ text (label machine) ]
            , div [ class "sub" ] [ text (machine.address ++ describe machine) ]
            ]
        , td [ class "sig" ] [ text (signalText machine) ]
        , td [ class "act" ]
            [ if chosen then
                span [ class "sub" ] [ text "in use" ]

              else
                button [ onClick (Send "select" [ ( "address", E.string machine.address ) ]) ] [ text "Use" ]
            ]
        ]


describe : Machine -> String
describe machine =
    String.concat
        [ if machine.classic then
            ""

          else
            "  not Bluetooth Classic"
        , if machine.bonded then
            "  paired"

          else
            ""
        ]


signalText : Machine -> String
signalText machine =
    case machine.signal of
        Just dbm ->
            String.fromInt dbm
                ++ " dBm"
                ++ (if dbm < -80 then
                        " (weak)"

                    else
                        ""
                   )

        Nothing ->
            "not in range"



-- THE PAIRING QUESTION


question : Model -> State -> Question -> Html Msg
question model state ask =
    let
        who =
            Maybe.map label state.machine |> Maybe.withDefault "the machine"

        code =
            Maybe.withDefault "" ask.code

        cancel =
            button [ onClick (Send "answer" [ ( "accept", E.bool False ) ]) ] [ text "Cancel" ]

    in
    div [ class "card ask" ]
        (case ask.kind of
            "pincode" ->
                [ p [] [ text ("Bluetooth PIN for " ++ who ++ ". If the machine shows one, type it; otherwise try 0000.") ]
                , Html.form [ class "row-btns", onSubmit SendReply ]
                    [ input [ value model.reply, onInput ReplyTyped, inputmode "numeric", autocomplete False, attribute "autofocus" "", attribute "aria-label" "Bluetooth PIN" ] []
                    , button [ class "go" ] [ text "Send" ]
                    , cancel
                    ]
                ]

            "passkey" ->
                [ p [] [ text ("Type the six-digit code " ++ who ++ " is showing.") ]
                , Html.form [ class "row-btns", onSubmit SendReply ]
                    [ input [ value model.reply, onInput ReplyTyped, inputmode "numeric", autocomplete False, attribute "autofocus" "", attribute "aria-label" "Six-digit code" ] []
                    , button [ class "go" ] [ text "Send" ]
                    , cancel
                    ]
                ]

            "confirm" ->
                [ p [] [ text ("Does " ++ who ++ " show this code?") ]
                , p [ class "code" ] [ text code ]
                , div [ class "row-btns" ]
                    [ button [ class "go", onClick (Send "answer" [ ( "accept", E.bool True ) ]) ] [ text "It matches" ]
                    , button [ onClick (Send "answer" [ ( "accept", E.bool False ) ]) ] [ text "It does not" ]
                    ]
                ]

            "authorize" ->
                [ p [] [ text (who ++ " wants to pair.") ]
                , div [ class "row-btns" ]
                    [ button [ class "go", onClick (Send "answer" [ ( "accept", E.bool True ) ]) ] [ text "Allow" ]
                    , button [ onClick (Send "answer" [ ( "accept", E.bool False ) ]) ] [ text "Deny" ]
                    ]
                ]

            _ ->
                [ p [] [ text ("Enter this code on " ++ who ++ ".") ]
                , p [ class "code" ] [ text code ]
                ]
        )



-- READINGS


readings : State -> List (Html Msg)
readings state =
    case state.reading of
        Nothing ->
            []

        Just reading ->
            [ div [ class "card readings" ] (List.map section reading.sections) ]


section : Section -> Html Msg
section content =
    div [ class "section" ]
        [ h2 [] [ text content.title ]
        , case ( content.problem, content.fields ) of
            ( Just why, _ ) ->
                p [ class "failed" ] [ text why ]

            ( Nothing, [] ) ->
                p [ class "muted" ] [ text "Nothing returned." ]

            ( Nothing, fields ) ->
                dl [] (List.map field fields)
        ]


field : Field -> Html Msg
field one =
    div [ class "row" ]
        [ dt [] [ text one.label ]
        , dd [] [ text one.value ]
        ]



-- ACTIVITY


activity : State -> Html Msg
activity state =
    details []
        [ summary [] [ text "Activity" ]
        , pre [ class "log" ] (List.map line state.log)
        ]


line : Line -> Html Msg
line one =
    span [ class one.level ] [ text (one.at ++ "  " ++ one.text ++ "\n") ]


footer : State -> Html Msg
footer state =
    p [ class "foot" ] [ text ("airsupply " ++ state.version ++ ". Reads only; it never writes to the machine.") ]



-- WIRING


inputmode : String -> Attribute msg
inputmode =
    attribute "inputmode"


main : Program () Model Msg
main =
    Browser.element
        { init = init
        , update = update
        , view = view
        , subscriptions = \_ -> fromAddon Incoming
        }
